import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from business_assignments import recover_business_assignments

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "assignment_tests"}):
    import server


def collection(rows=(), **kwargs):
    return SimpleNamespace(find=Mock(return_value=SimpleNamespace(to_list=AsyncMock(return_value=list(rows)))), **kwargs)


class AssignmentRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SimpleNamespace(businesses=collection([{"id": "live"}]))
        self.existing = {"id": "u", "business_ids": ["deleted", "live"], "pos_links": {"live": {"user_id": "pos-u"}}}

    async def test_admin_save_removes_only_stored_orphans(self):
        before = deepcopy(self.existing)
        changes = {"business_ids": ["deleted", "live", "unknown"], "name": "Updated"}
        targets = await recover_business_assignments(self.db, self.existing, changes, platform_admin=True)
        self.assertEqual(changes["business_ids"], ["live", "unknown"])
        self.assertEqual(targets, ["live"])
        self.assertEqual(self.existing, before)
        with patch.object(server, "db", self.db):
            with self.assertRaises(HTTPException) as raised:
                await server.validate_client_business_ids({"role": "platform_admin"}, changes["business_ids"])
        self.assertEqual(raised.exception.status_code, 400)

    async def test_profile_only_edit_also_recovers_orphans(self):
        changes = {"name": "Updated"}
        await recover_business_assignments(self.db, self.existing, changes, platform_admin=True)
        self.assertEqual(changes["business_ids"], ["live"])

    async def test_owner_cannot_silently_remove_another_assignment(self):
        changes = {"name": "Updated"}
        await recover_business_assignments(self.db, self.existing, changes, platform_admin=False)
        self.assertNotIn("business_ids", changes)

    async def test_missing_business_with_pos_link_requires_reconciliation(self):
        self.existing["pos_links"]["deleted"] = {"user_id": "pos-old"}
        with self.assertRaises(HTTPException) as raised:
            await recover_business_assignments(self.db, self.existing, {}, platform_admin=True)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "BUSINESS_LINK_REPAIR_REQUIRED")

    async def test_legacy_pos_link_is_protected(self):
        self.existing.update(pos_external_id="pos-old", pos_business_id="deleted")
        with self.assertRaises(HTTPException):
            await recover_business_assignments(self.db, self.existing, {}, platform_admin=True)

    async def test_valid_assignments_are_unchanged(self):
        self.existing["business_ids"] = ["live"]
        changes = {"name": "Updated"}
        await recover_business_assignments(self.db, self.existing, changes, platform_admin=True)
        self.assertEqual(changes, {"name": "Updated"})


class UserSaveRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_save_queues_valid_pos_target_and_preserves_original_for_concurrency(self):
        actor = {"id": "admin", "email": "admin@example.test", "role": "platform_admin"}
        existing = {"id": "u", "email": "u@example.test", "role": "business_owner", "business_ids": ["deleted", "live"]}
        db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=existing)),
                             businesses=collection([{"id": "live"}]))
        with patch.object(server, "db", db), patch.object(server, "get_current_user", AsyncMock(return_value=actor)), \
             patch.object(server, "validate_business_access_many", AsyncMock()) as access, \
             patch.object(server, "require_business_management", AsyncMock()), \
             patch.object(server, "require_business_module_enabled", AsyncMock()), \
             patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
             patch.object(server.pos_profile_updates, "enqueue", AsyncMock()) as queue:
            response = server.Response()
            await server.update_user("u", server.UserUpdate(name="New name", business_ids=["deleted", "live"]), None, response)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(queue.call_args.args[0]["business_ids"], ["deleted", "live"])
        self.assertEqual(queue.call_args.args[1]["business_ids"], ["live"])
        self.assertEqual(queue.call_args.kwargs["original_sync_business_ids"], ["live"])
        access.assert_awaited_once_with(actor, ["live"])

    async def test_sync_button_recovers_the_same_deleted_assignments(self):
        actor = {"id": "admin", "email": "admin@example.test", "role": "platform_admin"}
        target = {"id": "u", "email": "u@example.test", "business_ids": ["deleted", "live"]}
        db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=target)),
                             businesses=collection([{"id": "live"}]),
                             pos_profile_jobs=SimpleNamespace(find_one=AsyncMock(return_value=None)))
        with patch.object(server, "db", db), patch.object(server, "get_current_user", AsyncMock(return_value=actor)), \
             patch.object(server, "validate_business_access", AsyncMock()) as access, \
             patch.object(server, "require_business_module_enabled", AsyncMock()), \
             patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
             patch.object(server.pos_profile_updates, "enqueue", AsyncMock(return_value={"status": "pending"})) as queue:
            await server.sync_user_to_pos("u", None)
        self.assertEqual(queue.call_args.args[1], {"business_ids": ["live"]})
        self.assertEqual(queue.call_args.kwargs["original_sync_business_ids"], ["live"])
        access.assert_awaited_once_with(actor, "live")


class ManualSyncQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_sync_queues_without_contacting_rate_limited_pos(self):
        with patch.object(server, "get_pos_bridge_request_user", AsyncMock(return_value={"role": "platform_admin"})), \
             patch.object(server, "validate_pos_admin_business", AsyncMock()), \
             patch.object(server, "require_pos_bridge_access", AsyncMock()), \
             patch.object(server, "pos_read_filter", AsyncMock(return_value={})), \
             patch.object(server, "pos_headers_for_admin_business", AsyncMock()), \
             patch.object(server.pos_sync_worker, "enqueue_snapshot", AsyncMock(return_value={"id": "snapshot"})) as queue, \
             patch.object(server, "pos_bridge_request", AsyncMock()) as outgoing:
            result = await server.queue_pos_bridge_resource("products", None, "live")
        self.assertEqual(result["status"], "queued")
        queue.assert_awaited_once_with("products", "live", priority=100)
        outgoing.assert_not_awaited()

    async def test_manual_sync_requires_a_business(self):
        with patch.object(server, "get_pos_bridge_request_user", AsyncMock(return_value={"role": "platform_admin"})), \
             patch.object(server.pos_sync_worker, "enqueue_snapshot", AsyncMock()) as queue:
            with self.assertRaises(HTTPException) as raised:
                await server.queue_pos_bridge_resource("products", None, None)
        self.assertEqual(raised.exception.status_code, 400)
        queue.assert_not_awaited()

    async def test_manual_sync_rejects_other_tenant_before_queueing(self):
        with patch.object(server, "get_pos_bridge_request_user", AsyncMock(return_value={"role": "business_owner"})), \
             patch.object(server, "validate_pos_admin_business", AsyncMock(side_effect=HTTPException(403, "Access denied"))), \
             patch.object(server.pos_sync_worker, "enqueue_snapshot", AsyncMock()) as queue:
            with self.assertRaises(HTTPException):
                await server.queue_pos_bridge_resource("products", None, "other")
        queue.assert_not_awaited()


class BusinessDeletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SimpleNamespace(
            businesses=SimpleNamespace(find_one=AsyncMock(return_value={"id": "b"}), delete_one=AsyncMock(return_value=SimpleNamespace(deleted_count=1))),
            users=SimpleNamespace(find_one=AsyncMock(return_value=None), update_many=AsyncMock()),
            clients=SimpleNamespace(update_many=AsyncMock()),
            pos_profile_jobs=SimpleNamespace(find_one=AsyncMock(return_value=None)),
            pos_provisioning_jobs=SimpleNamespace(find_one=AsyncMock(return_value=None)),
        )
        for name in ["outlets", "business_modules", "feature_flags", "settings", "integrations"]:
            setattr(self.db, name, SimpleNamespace(delete_many=AsyncMock()))

    async def delete(self):
        with patch.object(server, "db", self.db), \
             patch.object(server, "get_current_user", AsyncMock(return_value={"role": "platform_admin", "id": "a", "email": "a@example.test"})), \
             patch.object(server, "create_audit_log", AsyncMock()):
            return await server.delete_business("b", None)

    async def test_unlinked_deletion_removes_memberships_and_revokes_sessions(self):
        await self.delete()
        self.db.users.update_many.assert_awaited_once_with({"business_ids": "b"}, {"$pull": {"business_ids": "b"}, "$inc": {"token_version": 1}})
        self.db.clients.update_many.assert_awaited_once_with({"business_ids": "b"}, {"$pull": {"business_ids": "b"}})

    async def test_linked_business_cannot_be_deleted_only_on_admincore(self):
        self.db.businesses.find_one.return_value = {"id": "b", "pos_external_id": "pos-b"}
        with self.assertRaises(HTTPException) as raised:
            await self.delete()
        self.assertEqual(raised.exception.status_code, 409)
        self.db.businesses.delete_one.assert_not_awaited()

    async def test_pending_profile_blocks_deletion(self):
        self.db.pos_profile_jobs.find_one.return_value = {"_id": "job"}
        with self.assertRaises(HTTPException):
            await self.delete()
        self.db.businesses.delete_one.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
