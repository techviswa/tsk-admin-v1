import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "admincore_sync_tests"}):
    import server


class EntitlementSyncTests(unittest.IsolatedAsyncioTestCase):
    def fixtures(self):
        business = {"id": "local-a", "pos_external_id": "pos-a", "pos_tenant_id": "tenant-a"}
        database = SimpleNamespace(businesses=SimpleNamespace(
            find_one=AsyncMock(return_value=business), update_one=AsyncMock()))
        return database

    async def test_applied_marker_requires_pos_acknowledgement(self):
        database = self.fixtures()
        with patch.object(server, "db", database), \
             patch.object(server, "get_business_entitlements", AsyncMock(return_value={"features": {"kot.basic": True}})), \
             patch.object(server, "pos_headers_for_admin_business", AsyncMock(return_value={"x-tenant-id": "tenant-a"})), \
             patch.object(server, "pos_core_session_request", AsyncMock(side_effect=server.HTTPException(503, "offline"))):
            with self.assertRaises(server.HTTPException):
                await server.process_pos_change("tenant-entitlements", "local-a")
        database.businesses.update_one.assert_not_awaited()

    async def test_worker_sends_actual_grants_with_verified_scope(self):
        database = self.fixtures()
        with patch.object(server, "db", database), \
             patch.object(server, "get_business_entitlements", AsyncMock(return_value={"features": {"kot.basic": True}})), \
             patch.object(server, "pos_headers_for_admin_business", AsyncMock(return_value={"x-tenant-id": "tenant-a"})), \
             patch.object(server, "pos_core_session_request", AsyncMock(return_value={"data": {}})) as send:
            result = await server.process_pos_change("tenant-entitlements", "local-a")
        self.assertEqual(result["status"], "success")
        self.assertIn("kot", send.await_args.kwargs["json"]["enabled_features"])
        self.assertEqual(send.await_args.args[:2], ("PUT", "admincore/tenants/pos-a/subscription"))
        self.assertEqual(send.await_args.kwargs["extra_headers"]["x-tenant-id"], "tenant-a")
        database.businesses.update_one.assert_awaited_once()

    async def test_import_cannot_change_existing_tenant_identity(self):
        database = self.fixtures()
        with patch.object(server, "db", database):
            with self.assertRaises(server.HTTPException) as error:
                await server.sync_bridge_business({"id": "pos-a", "tenantId": "other-tenant"}, {}, "now")
        self.assertEqual(error.exception.status_code, 409)
        database.businesses.update_one.assert_not_awaited()

    async def test_inactive_subscription_retains_history_feature_access(self):
        database = self.fixtures()
        with patch.object(server, "db", database), \
             patch.object(server, "get_business_entitlements", AsyncMock(return_value={"subscription_active": False})), \
             patch.object(server, "pos_core_session_request", AsyncMock()) as send:
            result = await server.process_pos_change("tenant-entitlements", "local-a")
        self.assertEqual(result["skipped"], "inactive_subscription")
        send.assert_not_awaited()

    async def test_unknown_notification_cannot_invent_a_business(self):
        database = self.fixtures()
        database.businesses.find_one = AsyncMock(return_value=None)
        request = SimpleNamespace(json=AsyncMock(return_value={"id": "old-event", "resource": "staff",
            "business_id": "deleted-fixture", "tenant_id": "deleted-tenant"}))
        with patch.object(server, "db", database), \
             patch.object(server, "require_pos_bridge_sync_key"), \
             patch.object(server, "get_pos_bridge_system_user", AsyncMock(return_value={})), \
             patch.object(server, "sync_bridge_business", AsyncMock()) as create:
            with self.assertRaises(server.HTTPException) as error:
                await server.receive_pos_bridge_sync_status(request)
        self.assertEqual(error.exception.status_code, 409)
        create.assert_not_awaited()
