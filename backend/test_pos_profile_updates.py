import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
from pos_profile_updates import PosProfileUpdates


class ProfileRecoveryTests(unittest.IsolatedAsyncioTestCase):
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
