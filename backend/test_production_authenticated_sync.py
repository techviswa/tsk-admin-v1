import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "admincore_guard_tests"}):
    import server


class AuthenticatedSyncTests(unittest.IsolatedAsyncioTestCase):
    def test_rate_limit_stops_followup_http_requests(self):
        response = SimpleNamespace(status_code=429, headers={"Retry-After": "7"})
        from unittest.mock import Mock
        session = SimpleNamespace(request=Mock(return_value=response))
        with patch.object(server, "POS_CORE_RATE_LIMIT_UNTIL", 0), \
             patch.object(server, "pos_next_request_at", 0), \
             patch.object(server.time, "time", return_value=100):
            for _ in range(2):
                with self.assertRaises(server.HTTPException) as error:
                    server.send_pos_request(session, "GET", "https://pos.invalid/api/products")
                self.assertEqual(error.exception.status_code, 429)
                self.assertEqual(error.exception.detail["retry_after_seconds"], 7)
            session.request.assert_called_once()

    async def test_failed_provisioning_records_step_and_retry_history(self):
        jobs = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1, matched_count=1)))
        database = SimpleNamespace(pos_provisioning_jobs=jobs, businesses=SimpleNamespace(update_one=AsyncMock()))
        async def fail_owner(*args, **kwargs):
            await kwargs["progress"]("owner")
            raise server.HTTPException(status_code=502, detail="POS unavailable")
        with patch.object(server, "db", database), \
             patch.object(server, "provision_business_end_to_end", side_effect=fail_owner), \
             patch.object(server, "mark_business_pos_status", new_callable=AsyncMock):
            await server.run_pos_provisioning_job({"id": "job", "business_id": "a", "attempts": 0, "max_attempts": 5})
        final_update = jobs.update_one.call_args.args[1]
        self.assertEqual(final_update["$set"]["status"], "retrying")
        entry = final_update["$push"]["history"]["$each"][0]
        self.assertEqual(entry["step"], "owner")
        self.assertEqual(entry["attempt"], 1)
        self.assertEqual(final_update["$push"]["history"]["$slice"], -100)
        database.businesses.update_one.assert_awaited_once()

    async def test_user_creation_is_accepted_and_queued_without_pos_call(self):
        account = {"id": "admin", "role": "platform_admin", "email": "admin@example.test"}
        users = SimpleNamespace(find_one=AsyncMock(side_effect=[account, None, {"id": "staff", "email": "staff@example.test"}]),
                                insert_one=AsyncMock(), count_documents=AsyncMock(return_value=0), delete_one=AsyncMock())
        database = SimpleNamespace(users=users, businesses=SimpleNamespace(find_one=AsyncMock(return_value={"id": "a"})))
        with patch.object(server, "db", database), patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
             patch.object(server, "validate_client_business_ids", new_callable=AsyncMock), \
             patch.object(server, "require_business_module_enabled", new_callable=AsyncMock), \
             patch.object(server, "enforce_limit", new_callable=AsyncMock), \
             patch.object(server, "create_audit_log", new_callable=AsyncMock), \
             patch.object(server.pos_profile_updates, "enqueue", new_callable=AsyncMock) as enqueue, \
             patch.object(server, "push_admin_user_to_pos", new_callable=AsyncMock) as push:
            token = server.create_access_token(account["id"], account["email"])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
                result = await client.post("/api/users", json={"name": "Staff", "email": "staff@example.test", "password": "password123", "role": "staff", "business_ids": ["a"]}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(result.status_code, 202, result.text)
        self.assertEqual(result.json()["pos_update_status"], "pending")
        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.call_args.kwargs["operation"], "create")
        push.assert_not_awaited()
        users.delete_one.assert_not_awaited()

    async def test_authenticated_export_accepts_own_scope_and_rejects_wrong_scope(self):
        for own in ["a", "b"]:
            account = {"id": own, "email": f"{own}@example.test", "role": "business_owner", "business_ids": [own]}
            business = {"id": own, "pos_external_id": f"pos-{own}", "pos_tenant_id": f"tenant-{own}"}
            database = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=account)),
                                       businesses=SimpleNamespace(find_one=AsyncMock(return_value=business)))
            with patch.object(server, "db", database), \
                 patch.object(server, "require_pos_bridge_access", new_callable=AsyncMock), \
                 patch.object(server, "pos_bridge_request", new_callable=AsyncMock) as export:
                token = server.create_access_token(account["id"], account["email"])
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
                    for tenant, expected in [(f"tenant-{own}", 200), ("wrong-tenant", 409)]:
                        export.return_value = [{"id": "product", "business_id": f"pos-{own}", "tenant_id": tenant}]
                        result = await client.get("/api/pos-bridge/proxy/products", params={"business_id": own}, headers={"Authorization": f"Bearer {token}"})
                        self.assertEqual(result.status_code, expected, result.text)

    async def test_profile_save_returns_accepted_without_pos_call_or_secret(self):
        account = {"id": "admin", "role": "platform_admin", "email": "admin@example.test"}
        target = {"id": "staff", "role": "staff", "business_ids": ["a"], "email": "staff@example.test", "password_hash": "private-hash"}
        database = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(side_effect=lambda query, *args: account if query.get("id") == "admin" else target)),
                                   businesses=SimpleNamespace(find_one=AsyncMock(return_value={"id": "a"})))
        with patch.object(server, "db", database), patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
             patch.object(server, "require_business_module_enabled", new_callable=AsyncMock), \
             patch.object(server.pos_profile_updates, "enqueue", new_callable=AsyncMock) as enqueue, \
             patch.object(server, "push_admin_user_to_pos", new_callable=AsyncMock) as push:
            token = server.create_access_token(account["id"], account["email"])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
                result = await client.put("/api/users/staff", json={"name": "Updated name"}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(result.status_code, 202, result.text)
        self.assertEqual(result.json()["pos_update_status"], "pending")
        self.assertNotIn("password_hash", result.json())
        enqueue.assert_awaited_once()
        push.assert_not_awaited()

    async def test_authenticated_tenants_cannot_read_or_queue_each_others_data(self):
        for own, other in [("a", "b"), ("b", "a")]:
            account = {"id": f"owner-{own}", "email": f"{own}@example.test", "role": "business_owner", "business_ids": [own]}
            database = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=account)),
                businesses=SimpleNamespace(find_one=AsyncMock(side_effect=lambda query, *args: {"id": query["id"]})))
            with patch.object(server, "db", database), \
                 patch.object(server, "pos_bridge_request", new_callable=AsyncMock) as export, \
                 patch.object(server.pos_sync_worker, "enqueue_snapshot", new_callable=AsyncMock) as queue:
                token = server.create_access_token(account["id"], account["email"])
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
                    headers = {"Authorization": f"Bearer {token}"}
                    for resource in ["products", "bills", "payments", "customers", "inventory", "reports", "staff-shifts"]:
                        result = await client.get(f"/api/pos-bridge/proxy/{resource}", params={"business_id": other}, headers=headers)
                        self.assertEqual(result.status_code, 403, result.text)
                    result = await client.post("/api/pos-bridge/sync-all", params={"business_id": other}, headers=headers)
                    self.assertEqual(result.status_code, 403, result.text)
                export.assert_not_awaited()
                queue.assert_not_awaited()

    async def test_unauthenticated_sync_is_rejected(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
            result = await client.post("/api/pos-bridge/sync-all", params={"business_id": "a"})
        self.assertEqual(result.status_code, 401)

    async def test_provisioning_resumes_after_owner_checkpoint(self):
        business = {"id": "a", "pos_external_id": "pos-a", "pos_tenant_id": "tenant-a"}
        owner = {"id": "owner", "email": "owner@example.test", "pos_links": {"a": {"user_id": "pos-owner", "business_id": "pos-a", "tenant_id": "tenant-a"}}}
        database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value=business)),
                                   users=SimpleNamespace(find_one=AsyncMock(return_value=owner)))
        steps = {"business": {"business_id": "pos-a", "tenant_id": "tenant-a"}, "owner_id": "owner"}
        with patch.object(server, "db", database), patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.invalid"), \
             patch.object(server, "mark_business_pos_status", new_callable=AsyncMock) as mark, \
             patch.object(server, "provision_admin_business_to_pos", new_callable=AsyncMock) as provision, \
             patch.object(server, "push_admin_user_to_pos", new_callable=AsyncMock) as push, \
             patch.object(server, "ensure_business_owner_user", new_callable=AsyncMock, return_value=owner), \
             patch.object(server, "ensure_default_outlet_for_business", new_callable=AsyncMock, return_value={"pos_external_id": "outlet", "pos_synced": True}):
            result = await server.provision_business_end_to_end("a", steps=steps, enqueue_on_failure=False)
        self.assertTrue(result["configured"])
        provision.assert_not_awaited()
        push.assert_not_awaited()
        self.assertEqual(mark.call_args.args[1], "synced")
        self.assertEqual(mark.call_args.kwargs["extra"]["pos_owner_id"], "pos-owner")
