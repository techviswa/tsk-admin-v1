import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "read_readiness_tests"}):
    import server


class PosReadReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_link_is_not_operational_and_reads_cannot_enqueue_impossible_jobs(self):
        business = {"status": "active", "pos_external_id": "source", "pos_synced": True}
        sanitized = server.sanitize_business_doc(business)
        self.assertEqual(sanitized["pos_connection_state"], "setup_required")
        self.assertFalse(sanitized["operational_ready"])
        database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value=business)))
        with patch.object(server, "db", database), patch.object(server.pos_sync_worker, "enqueue_snapshot", AsyncMock()) as queue:
            for resource in ("outlets", "products", "attendance"):
                result = await server.enqueue_pos_read_snapshot(resource, "local")
                self.assertEqual(result["status"], "setup_required")
            queue.assert_not_awaited()
            with patch.object(server, "POS_CORE_API_BASE_URL", "http://pos.test"):
                self.assertTrue(await server.is_pos_connected_business("local"), "Partial source links must remain POS-owned")

    async def test_complete_mapping_uses_the_durable_queue(self):
        database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value={"pos_external_id": "source", "pos_tenant_id": "tenant"})))
        with patch.object(server, "db", database), patch.object(server.pos_sync_worker, "enqueue_snapshot", AsyncMock(return_value={"status": "pending"})) as queue:
            self.assertEqual((await server.enqueue_pos_read_snapshot("products", "local"))["status"], "pending")
            queue.assert_awaited_once_with("products", "local", fresh_for_seconds=60)
