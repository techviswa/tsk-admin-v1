import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import mfa

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "admincore_mfa_tests"}):
    import server

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890", the RFC 6238 test key


class FakeCollection:
    """Just enough of a Mongo collection for the queries the MFA/login code issues."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def _match(self, doc, query):
        for key, cond in query.items():
            if key == "$or":
                if not any(self._match(doc, sub) for sub in cond):
                    return False
            elif isinstance(cond, dict):
                value = doc.get(key)
                if "$lt" in cond and not (value is not None and value < cond["$lt"]):
                    return False
                if "$exists" in cond and (key in doc) != cond["$exists"]:
                    return False
                if "$in" in cond and value not in cond["$in"]:
                    return False
            elif doc.get(key) != cond:
                return False
        return True

    async def find_one(self, query, projection=None):
        return next((dict(d) for d in self.docs if self._match(d, query)), None)

    async def update_one(self, query, update, upsert=False):
        target = next((d for d in self.docs if self._match(d, query)), None)
        if target is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0)
            target = {k: v for k, v in query.items() if not k.startswith("$") and not isinstance(v, dict)}
            self.docs.append(target)
        target.update(update.get("$set", {}))
        for key in update.get("$unset", {}):
            target.pop(key, None)
        for key, amount in update.get("$inc", {}).items():
            target[key] = target.get(key, 0) + amount
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def delete_one(self, query):
        self.docs = [d for d in self.docs if not self._match(d, query)]

    async def delete_many(self, query):
        keys = query["_id"]["$in"]
        self.docs = [d for d in self.docs if d.get("_id") not in keys]


class TotpPrimitiveTests(unittest.TestCase):
    def test_rfc6238_vector(self):
        # RFC 6238 appendix B, T=59s -> 94287082; the last six digits are the 6-digit code.
        self.assertEqual(mfa._hotp(RFC_SECRET, 1), "287082")
        self.assertEqual(mfa.verify_totp(RFC_SECRET, "287082", now=59), 1)

    def test_clock_skew_window_is_one_step(self):
        self.assertEqual(mfa.verify_totp(RFC_SECRET, "287082", now=59 + 30), 1)
        self.assertIsNone(mfa.verify_totp(RFC_SECRET, "287082", now=59 + 90))

    def test_replay_of_a_used_step_is_refused(self):
        self.assertIsNone(mfa.verify_totp(RFC_SECRET, "287082", now=59, last_step=1))

    def test_malformed_codes_are_rejected(self):
        for bad in (None, "", "12345", "1234567", "abcdef", "28708 2x"):
            self.assertIsNone(mfa.verify_totp(RFC_SECRET, bad, now=59))
        self.assertEqual(mfa.verify_totp(RFC_SECRET, "287 082", now=59), 1)

    def test_secret_encryption_roundtrip_and_key_binding(self):
        token = mfa.encrypt_secret(RFC_SECRET)
        self.assertNotIn(RFC_SECRET, token)
        self.assertEqual(mfa.decrypt_secret(token), RFC_SECRET)
        with patch.dict(os.environ, {"MFA_ENCRYPTION_KEY": "a-different-key"}):
            self.assertIsNone(mfa.decrypt_secret(token))
        self.assertIsNone(mfa.decrypt_secret("garbage"))

    def test_recovery_codes_are_single_use(self):
        codes, hashes = mfa.new_recovery_codes()
        self.assertEqual(len(set(codes)), mfa.RECOVERY_CODE_COUNT)
        self.assertNotIn(codes[0], hashes)
        remaining = mfa.consume_recovery_code(hashes, codes[0].upper())
        self.assertEqual(len(remaining), len(hashes) - 1)
        self.assertIsNone(mfa.consume_recovery_code(remaining, codes[0]))
        self.assertIsNone(mfa.consume_recovery_code(hashes, "nope-nope-nope"))

    def test_provisioning_uri(self):
        uri = mfa.provisioning_uri(RFC_SECRET, "a@x.com")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn(f"secret={RFC_SECRET}", uri)


class MfaFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1_700_000_000.0
        self.user = {"id": "u1", "email": "root@x.com", "name": "Root", "role": "platform_admin", "business_ids": [],
                     "status": "active", "password_hash": server.hash_password("correct-horse-battery")}
        self.db = SimpleNamespace(
            users=SimpleNamespace(find_one=AsyncMock(side_effect=self._find_user)),
            user_mfa=FakeCollection(),
            login_attempts=FakeCollection(),
            audit_logs=SimpleNamespace(insert_one=AsyncMock()),
        )
        self.patches = [patch.object(server, "db", self.db), patch.object(mfa.time, "time", side_effect=lambda: self.now)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def _find_user(self, query, projection=None):
        return dict(self.user) if query.get("id", self.user["id"]) == self.user["id"] and query.get("email", self.user["email"]) == self.user["email"] else None

    def request(self, path="/api/auth/mfa/setup", token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return SimpleNamespace(cookies={}, headers=headers, url=SimpleNamespace(path=path), client=SimpleNamespace(host="9.9.9.9"))

    @staticmethod
    def session_cookie_set(response):
        return any(name == b"set-cookie" and value.startswith(b"access_token=") and b"HttpOnly" in value for name, value in response.raw_headers)

    def code_at(self, secret, when):
        return mfa._hotp(secret, int(when // 30))

    async def enroll(self):
        access = server.create_access_token("u1", "root@x.com")
        setup = await server.mfa_setup(self.request(token=access))
        self.assertEqual(setup["otpauth_uri"].count("secret="), 1)
        enabled = await server.mfa_enable(server.MfaCode(code=self.code_at(setup["secret"], self.now)), self.request(token=access))
        self.assertTrue(enabled["enabled"])
        return setup["secret"], enabled["recovery_codes"], access

    async def test_secret_is_never_stored_in_plaintext(self):
        secret, _, _ = await self.enroll()
        stored = self.db.user_mfa.docs[0]
        self.assertNotIn(secret, str(stored))
        self.assertTrue(stored["enabled"])
        self.assertNotIn("pending_secret_enc", stored)

    async def test_wrong_code_does_not_enable(self):
        access = server.create_access_token("u1", "root@x.com")
        await server.mfa_setup(self.request(token=access))
        with self.assertRaises(server.HTTPException) as raised:
            await server.mfa_enable(server.MfaCode(code="000000"), self.request(token=access))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertFalse(self.db.user_mfa.docs[0]["enabled"])

    async def test_password_alone_does_not_start_a_session_once_enrolled(self):
        await self.enroll()
        response = server.Response()
        result = await server.login(server.LoginRequest(email="root@x.com", password="correct-horse-battery"), self.request("/api/auth/login"), response)
        self.assertTrue(result["mfa_required"])
        self.assertNotIn("access_token", result)
        self.assertNotIn("set-cookie", {k.lower() for k in response.headers})

    async def test_mfa_token_cannot_be_used_as_an_access_token(self):
        mfa_token = server.create_mfa_token("u1", "root@x.com")
        with self.assertRaises(server.HTTPException) as raised:
            await server.get_current_user(self.request("/api/businesses", token=mfa_token))
        self.assertEqual(raised.exception.status_code, 401)

    async def test_full_login_with_second_factor_then_replay_is_refused(self):
        secret, _, _ = await self.enroll()
        self.now += 60  # a later time step than the one consumed while enrolling
        mfa_token = server.create_mfa_token("u1", "root@x.com")
        code = self.code_at(secret, self.now)
        response = server.Response()
        result = await server.mfa_verify(server.MfaVerifyRequest(mfa_token=mfa_token, code=code), self.request("/api/auth/mfa/verify"), response)
        self.assertEqual(result["user"]["id"], "u1")
        self.assertTrue(self.session_cookie_set(response))
        self.assertNotIn("access_token", result, "tokens stay in httpOnly cookies, not in the JSON body")
        with self.assertRaises(server.HTTPException) as replay:
            await server.mfa_verify(server.MfaVerifyRequest(mfa_token=mfa_token, code=code), self.request("/api/auth/mfa/verify"), server.Response())
        self.assertEqual(replay.exception.status_code, 401)

    async def test_recovery_code_logs_in_once(self):
        _, recovery, _ = await self.enroll()
        mfa_token = server.create_mfa_token("u1", "root@x.com")
        response = server.Response()
        verify = lambda: server.mfa_verify(server.MfaVerifyRequest(mfa_token=mfa_token, code=recovery[0]), self.request("/api/auth/mfa/verify"), response)
        self.assertEqual((await verify())["user"]["id"], "u1")
        self.assertTrue(self.session_cookie_set(response))
        with self.assertRaises(server.HTTPException):
            await verify()

    async def test_brute_forcing_the_code_locks_the_account_out(self):
        await self.enroll()
        mfa_token = server.create_mfa_token("u1", "root@x.com")
        attempt = lambda code: server.mfa_verify(server.MfaVerifyRequest(mfa_token=mfa_token, code=code), self.request("/api/auth/mfa/verify"), server.Response())
        for _ in range(5):
            with self.assertRaises(server.HTTPException) as raised:
                await attempt("000000")
            self.assertEqual(raised.exception.status_code, 401)
        with self.assertRaises(server.HTTPException) as locked:
            await attempt("000000")
        self.assertEqual(locked.exception.status_code, 429)

    async def test_disable_needs_password_and_code(self):
        secret, recovery, access = await self.enroll()
        with self.assertRaises(server.HTTPException):
            await server.mfa_disable(server.MfaDisable(password="wrong", code=recovery[0]), self.request(token=access))
        self.assertTrue(self.db.user_mfa.docs)
        result = await server.mfa_disable(server.MfaDisable(password="correct-horse-battery", code=recovery[1]), self.request(token=access))
        self.assertFalse(result["enabled"])
        self.assertFalse(self.db.user_mfa.docs)

    async def test_disable_is_forbidden_when_mfa_is_mandatory(self):
        _, recovery, access = await self.enroll()
        with patch.dict(os.environ, {"REQUIRE_PLATFORM_MFA": "true"}):
            with self.assertRaises(server.HTTPException) as raised:
                await server.mfa_disable(server.MfaDisable(password="correct-horse-battery", code=recovery[0]), self.request(token=access))
        self.assertEqual(raised.exception.status_code, 403)

    async def test_mandatory_mfa_blocks_unenrolled_platform_staff_except_enrollment(self):
        access = server.create_access_token("u1", "root@x.com")
        with patch.dict(os.environ, {"REQUIRE_PLATFORM_MFA": "true"}):
            with self.assertRaises(server.HTTPException) as raised:
                await server.get_current_user(self.request("/api/businesses", token=access))
            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(raised.exception.detail["code"], "MFA_ENROLLMENT_REQUIRED")
            self.assertEqual((await server.get_current_user(self.request("/api/auth/mfa/setup", token=access)))["id"], "u1")

    async def test_mandatory_mfa_ignores_tenant_users(self):
        self.user["role"] = "manager"
        access = server.create_access_token("u1", "root@x.com")
        with patch.dict(os.environ, {"REQUIRE_PLATFORM_MFA": "true"}):
            self.assertEqual((await server.get_current_user(self.request("/api/businesses", token=access)))["id"], "u1")


if __name__ == "__main__":
    unittest.main()
