import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import auth_guard

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "admincore_auth_tests"}):
    import server


class FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query):
        return self.docs.get(query["_id"])

    async def update_one(self, query, update, upsert=False):
        self.docs.setdefault(query["_id"], {"_id": query["_id"]}).update(update["$set"])

    async def delete_many(self, query):
        for key in query["_id"]["$in"]:
            self.docs.pop(key, None)


class LoginThrottleTests(unittest.IsolatedAsyncioTestCase):
    async def test_locks_after_repeated_failures_and_clears_on_success(self):
        col, now = FakeCollection(), datetime.now(timezone.utc)
        for _ in range(auth_guard.MAX_FAILED_ATTEMPTS - 1):
            await auth_guard.record_failed_attempt(col, "a@x.com", "1.1.1.1", now)
        self.assertEqual(await auth_guard.lockout_seconds_remaining(col, "a@x.com", "1.1.1.1", now), 0)
        await auth_guard.record_failed_attempt(col, "a@x.com", "1.1.1.1", now)
        self.assertGreater(await auth_guard.lockout_seconds_remaining(col, "a@x.com", "1.1.1.1", now), 0)
        # The account key locks regardless of which IP the attacker rotates to.
        self.assertGreater(await auth_guard.lockout_seconds_remaining(col, "A@x.com", "9.9.9.9", now), 0)
        later = now + auth_guard.LOCKOUT_WINDOW + timedelta(seconds=1)
        self.assertEqual(await auth_guard.lockout_seconds_remaining(col, "a@x.com", "1.1.1.1", later), 0)
        await auth_guard.clear_failed_attempts(col, "a@x.com", "1.1.1.1")
        self.assertEqual(col.docs.get("pair:a@x.com|1.1.1.1"), None)

    async def test_login_returns_429_while_locked(self):
        col = FakeCollection()
        for _ in range(auth_guard.MAX_FAILED_ATTEMPTS):
            await auth_guard.record_failed_attempt(col, "a@x.com", "testclient")
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="testclient"))
        with patch.object(server, "db", SimpleNamespace(login_attempts=col, users=SimpleNamespace(find_one=AsyncMock()))):
            with self.assertRaises(server.HTTPException) as raised:
                await server.login(server.LoginRequest(email="a@x.com", password="right"), request, server.Response())
        self.assertEqual(raised.exception.status_code, 429)


class SessionRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def _user_for(self, token_version):
        users = SimpleNamespace(find_one=AsyncMock(return_value={"id": "u1", "status": "active", "token_version": token_version}))
        return SimpleNamespace(users=users)

    def _request(self, token):
        return SimpleNamespace(cookies={}, headers={"Authorization": f"Bearer {token}"})

    async def test_token_from_before_logout_is_rejected(self):
        old = server.create_access_token("u1", "a@x.com", 0)
        with patch.object(server, "db", await self._user_for(1)):
            with self.assertRaises(server.HTTPException) as raised:
                await server.get_current_user(self._request(old))
        self.assertEqual(raised.exception.status_code, 401)

    async def test_current_version_token_is_accepted(self):
        token = server.create_access_token("u1", "a@x.com", 2)
        with patch.object(server, "db", await self._user_for(2)):
            user = await server.get_current_user(self._request(token))
        self.assertEqual(user["id"], "u1")

    async def test_legacy_tokens_without_version_still_work_until_first_logout(self):
        token = server.create_access_token("u1", "a@x.com")
        with patch.object(server, "db", await self._user_for(0)):
            self.assertEqual((await server.get_current_user(self._request(token)))["id"], "u1")


class RegistrationPolicyTests(unittest.TestCase):
    def test_disabled_by_default_in_production(self):
        with patch.dict(os.environ, {"ALLOW_SELF_REGISTRATION": ""}), \
             patch.object(server, "PRODUCTION_CONFIG_STATUS", {"production": True}):
            self.assertFalse(server.self_registration_enabled())

    def test_explicit_flag_wins(self):
        with patch.dict(os.environ, {"ALLOW_SELF_REGISTRATION": "true"}), \
             patch.object(server, "PRODUCTION_CONFIG_STATUS", {"production": True}):
            self.assertTrue(server.self_registration_enabled())

    def test_cors_has_no_wildcard_vercel_default(self):
        cors = next(m for m in server.app.user_middleware if m.cls.__name__ == "CORSMiddleware")
        self.assertFalse(cors.kwargs.get("allow_origin_regex"))


class CsrfTests(unittest.TestCase):
    def req(self, method, cookies=None, headers=None):
        return SimpleNamespace(method=method, cookies=cookies or {}, headers=headers or {})

    def test_cookie_session_needs_the_csrf_header_for_writes(self):
        self.assertTrue(server.csrf_rejected(self.req("POST", {"access_token": "t"})))
        self.assertFalse(server.csrf_rejected(self.req("POST", {"access_token": "t"}, {"x-requested-with": "XMLHttpRequest"})))

    def test_reads_bearer_calls_and_anonymous_calls_are_unaffected(self):
        self.assertFalse(server.csrf_rejected(self.req("GET", {"access_token": "t"})))
        self.assertFalse(server.csrf_rejected(self.req("POST", {"access_token": "t"}, {"authorization": "Bearer x"})))
        self.assertFalse(server.csrf_rejected(self.req("POST")))

    def test_tokens_are_not_echoed_in_json_by_default(self):
        with patch.dict(os.environ, {"EXPOSE_TOKENS_IN_BODY": ""}):
            self.assertNotIn("access_token", server.session_body({"ok": 1}, "a", "r"))
        with patch.dict(os.environ, {"EXPOSE_TOKENS_IN_BODY": "true"}):
            self.assertEqual(server.session_body({}, "a", "r")["access_token"], "a")


if __name__ == "__main__":
    unittest.main()
