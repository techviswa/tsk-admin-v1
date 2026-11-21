import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pymongo.errors import DuplicateKeyError

from fastapi import HTTPException
from pos_profile_updates import PosProfileUpdates
from pos_sync_worker import RejectedSyncUpdate


class ProfileRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_validation_rejection_before_any_business_allows_correction(self):
        event = await self.event()
        self.push.side_effect = HTTPException(502, detail={"status_code": 409, "response": {"error": {"code": "EMAIL_EXISTS", "message": "Email already exists"}}})
        with self.assertRaises(RejectedSyncUpdate):
            await self.service.process("profile", "user", event)
        self.db.users.update_one.assert_not_awaited()

    async def test_validation_error_after_partial_sync_preserves_recovery_guard(self):
        event = await self.event()
        self.push.side_effect = [{"data": {}}, HTTPException(502, detail={"status_code": 409, "response": {"error": {"code": "EMAIL_EXISTS"}}})]
        with self.assertRaises(HTTPException):
            await self.service.process("profile", "user", event)
        self.db.users.update_one.assert_not_awaited()

    async def test_ambiguous_server_failure_does_not_unlock_the_profile(self):
        event = await self.event()
        self.push.side_effect = HTTPException(502, detail={"status_code": 500, "response": {"error": {"code": "P2002"}}})
        with self.assertRaises(HTTPException):
            await self.service.process("profile", "user", event)
        self.db.users.update_one.assert_not_awaited()

    async def test_duplicate_save_returns_status_without_exposing_credentials(self):
        self.jobs.find_one_and_update.side_effect = DuplicateKeyError('unfinished')
        self.jobs.find_one = AsyncMock(return_value={"status": "retrying", "run_after": "2026-10-23T12:00:00Z", "event": {"password": "secret-hash"}})
        with self.assertRaises(HTTPException) as raised:
            await self.event()
        detail = raised.exception.detail
        self.assertEqual(detail["code"], "POS_PROFILE_UPDATE_UNFINISHED")
        self.assertEqual(detail["status"], "retrying")
        self.assertEqual(detail["retry_at"], "2026-10-23T12:00:00Z")
        self.assertNotIn('secret-hash', str(detail))

    def setUp(self):
        self.current = {"id": "user", "email": "old@example.test", "business_ids": ["a", "b"]}
        self.jobs = SimpleNamespace(find_one_and_update=AsyncMock(), update_one=AsyncMock())
        self.db = SimpleNamespace(pos_profile_jobs=self.jobs,
            users=SimpleNamespace(find_one=AsyncMock(return_value=self.current), update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1))),
            businesses=SimpleNamespace(update_many=AsyncMock()))
        self.push = AsyncMock(return_value={"data": {"id": "pos-user"}})
        self.service = PosProfileUpdates(self.db, self.push, AsyncMock())

    async def event(self):
        await self.service.enqueue(self.current, {"email": "new@example.test"}, "test-password-123", {"id": "admin", "email": "admin@example.test"})
        return deepcopy(self.jobs.find_one_and_update.call_args.args[1]["$set"]["event"])

    async def test_partial_failure_resumes_at_unfinished_business(self):
        event = await self.event()
        self.push.side_effect = [{"data": {}}, HTTPException(429, detail={"code": "POS_RATE_LIMITED"})]
        with self.assertRaises(HTTPException):
            await self.service.process("profile", "user", event)
        self.db.users.update_one.assert_not_awaited()
        self.assertEqual(self.jobs.update_one.call_args.args[1]["$addToSet"]["event.completed_business_ids"], "a")
        event["completed_business_ids"] = ["a"]
        self.push.reset_mock(side_effect=True)
        self.push.return_value = {"data": {"id": "pos-user"}}
        result = await self.service.process("profile", "user", event)
        self.assertEqual(result["status"], "success")
        self.push.assert_awaited_once()
        self.assertEqual(self.push.call_args.kwargs["target_business_id"], "b")
        self.db.users.update_one.assert_awaited_once()

    async def test_password_is_hashed_before_durable_storage(self):
        event = await self.event()
        self.assertTrue(event["password"].startswith("pbkdf2$120000$"))
        self.assertNotIn("test-password-123", str(event))

    async def test_concurrent_edit_is_not_overwritten(self):
        event = await self.event()
        self.current["email"] = "another@example.test"
        with self.assertRaises(HTTPException):
            await self.service.process("profile", "user", event)
        self.push.assert_not_awaited()
        self.db.users.update_one.assert_not_awaited()

    async def test_removed_business_is_disabled_before_local_membership_changes(self):
        event = await self.event()
        event["business_ids"] = ["b"]
        event["changes"]["business_ids"] = ["b"]
        await self.service.process("profile", "user", event)
        first = self.push.await_args_list[0]
        self.assertEqual(first.kwargs["target_business_id"], "a")
        self.assertEqual(first.args[0]["status"], "inactive")
        self.assertIsNone(first.args[1])

    async def test_deleted_unlinked_business_is_not_a_sync_target(self):
        self.current["business_ids"] = ["deleted", "b"]
        await self.service.enqueue(self.current, {"business_ids": ["b"], "name": "Updated"}, None,
                                   {"id": "admin", "email": "admin@example.test"}, original_sync_business_ids=["b"])
        event = deepcopy(self.jobs.find_one_and_update.call_args.args[1]["$set"]["event"])
        await self.service.process("profile", "user", event)
        self.push.assert_awaited_once()
        self.assertEqual(self.push.call_args.kwargs["target_business_id"], "b")
        self.assertEqual(event["original_business_ids"], ["deleted", "b"])
        self.assertEqual(self.db.users.update_one.await_args_list[0].args[1]["$set"]["business_ids"], ["b"])

    async def test_original_membership_changes_still_block_recovered_job(self):
        self.current["business_ids"] = ["deleted", "b"]
        await self.service.enqueue(self.current, {"business_ids": ["b"]}, None,
                                   {"id": "admin", "email": "admin@example.test"}, original_sync_business_ids=["b"])
        event = deepcopy(self.jobs.find_one_and_update.call_args.args[1]["$set"]["event"])
        self.current["business_ids"] = ["different"]
        with self.assertRaises(HTTPException):
            await self.service.process("profile", "user", event)
        self.push.assert_not_awaited()
