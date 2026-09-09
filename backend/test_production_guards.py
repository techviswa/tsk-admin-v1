import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

# Keep this suite independent of developer credentials and database availability.
with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "admincore_guard_tests"}):
    import server


class ProductionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_all_queues_every_resource_without_calling_pos(self):
        async def enqueue(resource, business_id):
            return {"id": f"pos-snapshot:{business_id}:{resource}", "status": "pending"}
        with patch.object(server, "get_current_user", new_callable=AsyncMock, return_value={"role": "platform_admin"}), \
             patch.object(server, "validate_pos_admin_business", new_callable=AsyncMock), \
             patch.object(server, "require_pos_bridge_access", new_callable=AsyncMock), \
             patch.object(server, "pos_headers_for_admin_business", new_callable=AsyncMock), \
             patch.object(server.pos_sync_worker, "enqueue_snapshot", new_callable=AsyncMock, side_effect=enqueue) as queue, \
             patch.object(server, "sync_pos_bridge_resource_for_system", new_callable=AsyncMock) as sync:
            with self.assertRaises(server.HTTPException):
                await server.sync_all_pos_bridge_resources(None, None)
            result = await server.sync_all_pos_bridge_resources(None, "a")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(set(result["results"]), set(server.POS_BRIDGE_RESOURCES))
        self.assertEqual(queue.await_count, len(server.POS_BRIDGE_RESOURCES))
        sync.assert_not_awaited()

    async def test_snapshot_does_not_repeat_dependency_exports(self):
        with patch.object(server, "get_pos_bridge_system_user", new_callable=AsyncMock, return_value={}), \
             patch.object(server, "sync_pos_bridge_resource_for_system", new_callable=AsyncMock, return_value={"status": "success"}) as sync:
            await server.process_pos_change("bills", "a", {"action": "snapshot"})
        sync.assert_awaited_once_with("bills", "a", {})

    async def test_unprovisioned_scope_is_not_fabricated(self):
        businesses = SimpleNamespace(find_one=AsyncMock(return_value={"id": "a"}), update_one=AsyncMock())
        with patch.object(server, "db", SimpleNamespace(businesses=businesses)):
            with self.assertRaises(server.HTTPException) as raised:
                await server.pos_headers_for_admin_business("a")
        self.assertEqual(raised.exception.status_code, 409)
        businesses.update_one.assert_not_awaited()

    async def test_linked_writes_skip_export_and_validate_response_scope(self):
        headers = {"business_id": "pos-a", "tenant_id": "tenant-a", "x-tenant-id": "tenant-a"}
        for resource, push in [("products", server.push_admin_product_to_pos), ("outlets", server.push_admin_outlet_to_pos)]:
            for returned_tenant in ["tenant-a", "other-tenant"]:
                collection = SimpleNamespace(update_one=AsyncMock())
                database = SimpleNamespace(**{resource: collection})
                record = {"id": "local", "business_id": "a", "name": "item", "pos_external_id": "pos-item", "pos_business_id": "pos-a", "pos_tenant_id": "tenant-a"}
                response = {"data": {"id": "pos-item", "business_id": "pos-a", "tenant_id": returned_tenant}}
                with patch.object(server, "db", database), patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
                     patch.object(server, "pos_headers_for_admin_business", new_callable=AsyncMock, return_value=headers), \
                     patch.object(server, "pos_bridge_request", new_callable=AsyncMock) as export, \
                     patch.object(server, "pos_core_session_request", new_callable=AsyncMock, return_value=response) as write:
                    if returned_tenant == "tenant-a":
                        await push(record)
                        collection.update_one.assert_awaited_once()
                    else:
                        with self.assertRaises(server.HTTPException):
                            await push(record)
                        collection.update_one.assert_not_awaited()
                    export.assert_not_awaited()
                    self.assertEqual(write.call_args.args[:2], ("PUT", f"admincore/{resource}/pos-item"))

    def test_request_gate_blocks_calls_after_first_rate_limit(self):
        session = SimpleNamespace(request=Mock(return_value=SimpleNamespace(status_code=429, headers={"Retry-After": "30"})))
        with patch.object(server, "POS_CORE_RATE_LIMIT_UNTIL", 0), patch.object(server, "pos_next_request_at", 0):
            for _ in range(2):
                with self.assertRaises(server.HTTPException) as raised:
                    server.send_pos_request(session, "GET", "https://pos.invalid/api/products")
                self.assertEqual(raised.exception.status_code, 429)
        session.request.assert_called_once()

    def test_rate_limit_respects_pos_retry_after(self):
        with patch.object(server, "POS_CORE_RATE_LIMIT_UNTIL", 0), patch.object(server.time, "time", return_value=100):
            self.assertEqual(server.mark_pos_rate_limited(SimpleNamespace(headers={"Retry-After": "12"})), 12)
            self.assertEqual(server.POS_CORE_RATE_LIMIT_UNTIL, 112)

    async def test_ensuring_verified_outlet_does_not_write_back_to_pos(self):
        outlet = {"id": "outlet", "pos_external_id": "pos-outlet", "pos_synced": True, "pos_business_id": "pos-a", "pos_tenant_id": "tenant-a"}
        database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value={"pos_external_id": "pos-a", "pos_tenant_id": "tenant-a"})), outlets=SimpleNamespace(find_one=AsyncMock(return_value=outlet), update_one=AsyncMock()))
        with patch.object(server, "db", database), patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
             patch.object(server, "push_admin_outlet_to_pos", new_callable=AsyncMock) as push:
            result = await server.ensure_default_outlet_for_business("a")
        self.assertEqual(result["pos_external_id"], "pos-outlet")
        push.assert_not_awaited()

    async def test_business_import_does_not_trigger_outbound_sync(self):
        database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value={"id": "a", "slug": "a"}), update_one=AsyncMock()))
        with patch.object(server, "db", database), \
             patch.object(server, "ensure_default_outlet_for_business", new_callable=AsyncMock) as outlet:
            await server.sync_bridge_business({"id": "pos-a", "tenant_id": "tenant-a"}, {"id": "admin"}, "now")
        self.assertFalse(outlet.call_args.kwargs["sync_to_pos"])

    def test_staff_export_alias_uses_registered_endpoint(self):
        self.assertEqual(server.pos_bridge_resource("staff"), server.pos_bridge_resource("staff-shifts"))

    async def test_pos_staff_notification_is_durably_accepted(self):
        payload = {"id": "event-a", "resource": "staff", "admincore_business_id": "a", "business_id": "pos-a", "tenant_id": "tenant-a", "action": "updated", "record_id": "user-a"}
        request = SimpleNamespace(json=AsyncMock(return_value=payload))
        with patch.object(server, "require_pos_bridge_sync_key"), \
             patch.object(server, "get_pos_bridge_system_user", new_callable=AsyncMock, return_value={}), \
             patch.object(server, "expected_pos_scope_for_business", new_callable=AsyncMock, return_value={"business_id": "pos-a", "tenant_id": "tenant-a"}), \
             patch.object(server.pos_sync_worker, "enqueue", new_callable=AsyncMock, return_value={"status": "pending"}) as enqueue:
            result = await server.receive_pos_bridge_sync_status(request)
        self.assertTrue(result["accepted"])
        self.assertEqual(enqueue.call_args.args, ("event-a", "staff-shifts", "a"))

    async def test_staff_email_change_preserves_local_identity(self):
        users = SimpleNamespace(find_one=AsyncMock(side_effect=[{"id": "local", "role": "staff", "business_ids": ["a"]}, None]), update_one=AsyncMock(), insert_one=AsyncMock())
        row = {"id": "pos-user", "email": "changed@example.test", "business_id": "pos-a", "tenant_id": "tenant-a"}
        with patch.object(server, "db", SimpleNamespace(users=users)), patch.object(server, "assert_pos_row_scope", new_callable=AsyncMock):
            result = await server.sync_bridge_staff_user(row, "a", "now")
        self.assertEqual(result, "local")
        self.assertEqual(users.update_one.call_args.args[1]["$set"]["email"], row["email"])
        self.assertEqual(users.update_one.call_args.args[1]["$set"]["pos_links.a"]["user_id"], "pos-user")
        users.insert_one.assert_not_awaited()

    async def test_staff_import_cannot_take_over_unrelated_or_platform_account(self):
        for account in [{"id": "other", "role": "staff", "business_ids": ["b"]}, {"id": "admin", "role": "platform_admin", "business_ids": ["a"]}]:
            users = SimpleNamespace(find_one=AsyncMock(side_effect=[None, account]), update_one=AsyncMock(), insert_one=AsyncMock())
            with patch.object(server, "db", SimpleNamespace(users=users)), patch.object(server, "assert_pos_row_scope", new_callable=AsyncMock):
                with self.assertRaises(server.HTTPException):
                    await server.sync_bridge_staff_user({"id": "pos-user", "email": "same@example.test"}, "a", "now")
            users.update_one.assert_not_awaited()
            users.insert_one.assert_not_awaited()

    async def test_delete_notifications_reconcile_all_supported_mirror_resources(self):
        for resource in set(server.POS_BRIDGE_RESOURCES) - {"businesses", "staff-shifts"}:
            collection = SimpleNamespace(delete_many=AsyncMock())
            with patch.object(server, "db", {server.pos_bridge_resource(resource)["collection"]: collection}), \
                 patch.object(server, "get_pos_bridge_system_user", new_callable=AsyncMock, return_value={}), \
                 patch.object(server, "expected_pos_scope_for_business", new_callable=AsyncMock, return_value={"business_id": "pos-a", "tenant_id": "tenant-a"}), \
                 patch.object(server, "sync_pos_bridge_resource_for_system", new_callable=AsyncMock, return_value={"status": "success", "synced": []}):
                await server.process_pos_change(resource, "a", {"action": "deleted", "record_id": "deleted-id"})
            collection.delete_many.assert_awaited_once_with({"business_id": "a", "pos_business_id": "pos-a", "pos_tenant_id": "tenant-a", "pos_external_id": "deleted-id"})

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
