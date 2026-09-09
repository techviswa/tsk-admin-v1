import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# Keep this suite independent of developer credentials and database availability.
with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "admincore_guard_tests"}):
    import server


class ProductionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_unlinked_user_email_edit_updates_scoped_existing_pos_account(self):
        scope = {"business_id": "pos-a", "tenant_id": "tenant-a"}
        business = {"id": "business-a", "pos_external_id": "pos-a", "pos_tenant_id": "tenant-a"}
        database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value=business)), users=SimpleNamespace(update_one=AsyncMock()))
        user = {"id": "local-user", "email": "new@example.test", "business_ids": ["business-a"]}
        for wrong_scope in [False, True]:
            row = {"id": "original-pos-user", "email": "old@example.test", **scope}
            if wrong_scope:
                row["business_id"] = "other-business"
            with patch.object(server, "db", database), patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
                 patch.object(server, "pos_headers_for_admin_business", new_callable=AsyncMock, return_value={}), \
                 patch.object(server, "ensure_default_outlet_for_business", new_callable=AsyncMock, return_value={"pos_external_id": "outlet-a"}), \
                 patch.object(server, "expected_pos_scope_for_business", new_callable=AsyncMock, return_value=scope), \
                 patch.object(server, "pos_bridge_request", new_callable=AsyncMock, return_value={"items": [row]}), \
                 patch.object(server, "pos_core_session_request", new_callable=AsyncMock, return_value={"id": "original-pos-user", **scope}) as write:
                if wrong_scope:
                    with self.assertRaises(server.HTTPException):
                        await server.push_admin_user_to_pos(user, previous_email="old@example.test")
                    write.assert_not_awaited()
                else:
                    await server.push_admin_user_to_pos(user, previous_email="old@example.test")
                    self.assertEqual(write.call_args.args, ("PUT", "admincore/staff/original-pos-user"))
                    self.assertEqual(write.call_args.kwargs["json"]["email"], "new@example.test")
                    self.assertNotIn("password", write.call_args.kwargs["json"])

    async def test_bill_change_refreshes_derived_resources(self):
        sync = AsyncMock(return_value={"status": "success", "error_count": 0})
        with patch.object(server, "get_pos_bridge_system_user", new_callable=AsyncMock, return_value={}), \
             patch.object(server, "sync_pos_bridge_resource_for_system", sync):
            await server.process_pos_change("bills", "business-a")
        self.assertEqual([call.args[0] for call in sync.await_args_list], ["bills", "payments", "customers", "reports", "products", "inventory"])
        self.assertTrue(all(call.args[1] == "business-a" for call in sync.await_args_list))

    async def test_delete_is_scoped_and_ignored_when_pos_record_still_exists(self):
        collection = SimpleNamespace(delete_many=AsyncMock())
        for records in [[], [{"external_id": "product-a"}]]:
            collection.delete_many.reset_mock()
            with patch.object(server, "db", {"products": collection}), \
                 patch.object(server, "get_pos_bridge_system_user", new_callable=AsyncMock, return_value={}), \
                 patch.object(server, "expected_pos_scope_for_business", new_callable=AsyncMock, return_value={"business_id": "pos-a", "tenant_id": "tenant-a"}), \
                 patch.object(server, "sync_pos_bridge_resource_for_system", new_callable=AsyncMock, return_value={"status": "success", "synced": records}):
                await server.process_pos_change("products", "business-a", {"action": "deleted", "record_id": "product-a"})
            if records:
                collection.delete_many.assert_not_awaited()
            else:
                collection.delete_many.assert_awaited_once_with({"business_id": "business-a", "pos_business_id": "pos-a", "pos_tenant_id": "tenant-a", "pos_external_id": "product-a"})

    async def test_native_payment_and_customer_exports_keep_identity_and_values(self):
        scope = {"business_id": "pos-a", "tenant_id": "tenant-a"}
        for resource in ["payments", "customers", "reports"]:
            row = {"id": "stable-id", **scope, "amount": 30, "status": "pending", "order_count": 7, "total_spent": 500}
            with patch.object(server, "expected_pos_scope_for_business", new_callable=AsyncMock, return_value=scope):
                result = await server.prepare_pos_bridge_rows(resource, {"resource": resource, "items": [row]}, "business-a")
            self.assertEqual(result, [row])

    async def test_missing_scope_is_never_inferred_for_derived_resources(self):
        scope = {"business_id": "pos-a", "tenant_id": "tenant-a"}
        for resource in ["payments", "customers", "reports", "businesses", "inventory"]:
            for row in [{"id": "row"}, {"id": "row", "business_id": "pos-a"}, {"id": "row", "business_id": "pos-b", "tenant_id": "tenant-a"}]:
                with patch.object(server, "expected_pos_scope_for_business", new_callable=AsyncMock, return_value=scope):
                    with self.assertRaises(server.HTTPException):
                        await server.prepare_pos_bridge_rows(resource, {"resource": resource, "items": [row]}, "business-a")

    async def test_pending_and_failed_businesses_block_operations(self):
        for status in ["pending", "failed", "not_configured"]:
            database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value={"pos_provisioning_status": status})))
            with patch.object(server, "db", database):
                for module in ["products", "outlets", "payments", "billing", "inventory", "reports", "tables"]:
                    with self.assertRaises(server.HTTPException) as raised:
                        await server.require_business_module_enabled("business-a", module)
                    self.assertEqual(raised.exception.status_code, 409)
                    self.assertEqual(raised.exception.detail["code"], "POS_BUSINESS_NOT_READY")

    async def test_pending_clears_previous_success_flag(self):
        update = AsyncMock()
        database = SimpleNamespace(businesses=SimpleNamespace(update_one=update))
        with patch.object(server, "db", database):
            await server.mark_business_pos_status("business-a", "pending")
        self.assertIs(update.call_args.args[1]["$set"]["pos_synced"], False)

    def test_pending_business_is_not_operational_even_with_stale_success(self):
        result = server.sanitize_business_doc({"status": "active", "pos_provisioning_status": "pending", "pos_synced": True})
        self.assertIs(result["operational_ready"], False)

    async def test_sync_requires_scope_before_network(self):
        with patch.object(server, "run_in_threadpool", new_callable=AsyncMock) as network:
            with self.assertRaises(server.HTTPException) as raised:
                await server.pos_bridge_request("products")
            self.assertEqual(raised.exception.status_code, 400)
            network.assert_not_awaited()

    async def test_owner_response_requires_id_business_and_tenant(self):
        business = {"id": "business-a", "pos_external_id": "pos-a", "pos_tenant_id": "tenant-a"}
        database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value=business)), users=SimpleNamespace(update_one=AsyncMock()))
        user = {"id": "user", "email": "owner@example.test", "role": "business_owner", "business_ids": ["business-a"]}
        responses = [{}, {"id": "owner"}, {"id": "owner", "business_id": "pos-b", "tenant_id": "tenant-a"},
                     {"id": "owner", "business_id": "pos-a", "tenant_id": "tenant-b"}]
        for response in responses:
            with patch.object(server, "db", database), patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
                 patch.object(server, "pos_headers_for_admin_business", new_callable=AsyncMock, return_value={}), \
                 patch.object(server, "ensure_default_outlet_for_business", new_callable=AsyncMock, return_value={"pos_external_id": "outlet-a"}), \
                 patch.object(server, "pos_core_session_request", new_callable=AsyncMock, return_value=response):
                with self.assertRaises(server.HTTPException):
                    await server.push_admin_user_to_pos(user, "test-password", target_business_id="business-a")
            database.users.update_one.assert_not_awaited()

    async def test_incomplete_outlet_never_marks_provisioning_synced(self):
        database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value={"pos_external_id": "pos-a", "pos_tenant_id": "tenant-a"})))
        with patch.object(server, "db", database), patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
             patch.object(server, "mark_business_pos_status", new_callable=AsyncMock) as mark, \
             patch.object(server, "provision_admin_business_to_pos", new_callable=AsyncMock, return_value={"business_id": "pos-a", "tenant_id": "tenant-a"}), \
             patch.object(server, "ensure_business_owner_user", new_callable=AsyncMock, return_value={"id": "owner"}), \
             patch.object(server, "push_admin_user_to_pos", new_callable=AsyncMock), \
             patch.object(server, "ensure_default_outlet_for_business", new_callable=AsyncMock, return_value={"id": "local-only"}):
            with self.assertRaises(server.HTTPException):
                await server.provision_business_end_to_end("business-a", enqueue_on_failure=False)
            self.assertEqual([call.args[1] for call in mark.await_args_list], ["pending", "failed"])


if __name__ == "__main__":
    unittest.main()
