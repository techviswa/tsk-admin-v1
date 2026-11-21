import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from fastapi import HTTPException
from pos_operations import OPERATIONS, reconcile_snapshot
from pos_sync_worker import PosSyncWorker

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "operations_tests"}):
    import server


class OperationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconciliation_retains_absent_rows_and_scopes_them_to_one_source(self):
        collection = SimpleNamespace(update_many=AsyncMock())
        await reconcile_snapshot(collection, business_id="local", scope={"business_id": "source", "tenant_id": "tenant"}, external_ids=["present"], now="now")
        query, update = collection.update_many.call_args.args
        self.assertEqual(query["business_id"], "local")
        self.assertEqual(query["pos_business_id"], "source")
        self.assertEqual(query["pos_tenant_id"], "tenant")
        self.assertEqual(query["pos_external_id"], {"$nin": ["present"]})
        self.assertFalse(update["$set"]["source_present"])

    async def test_partial_or_unpaged_exports_cannot_hide_cached_rows(self):
        with patch.object(server, "expected_pos_scope_for_business", AsyncMock()) as scope:
            await server.reconcile_complete_pos_snapshot("attendance", {}, {}, [], [], "b", "now")
            await server.reconcile_complete_pos_snapshot("attendance", {}, {"meta": {"pagination": {}}}, [], ["failed row"], "b", "now")
            await server.reconcile_complete_pos_snapshot("products", {}, {"meta": {"pagination": {"has_more": True}}}, [], [], "b", "now")
        scope.assert_not_awaited()
    async def test_read_poll_does_not_reset_exhausted_sync_retries(self):
        collection = SimpleNamespace(find_one=AsyncMock(return_value={"status": "failed"}), find_one_and_update=AsyncMock())
        result = await PosSyncWorker(collection, AsyncMock()).enqueue_snapshot("payroll", "b", fresh_for_seconds=60)
        self.assertEqual(result["status"], "failed")
        collection.find_one_and_update.assert_not_awaited()
    def test_every_new_module_has_scoped_export_and_module_gate(self):
        for key in OPERATIONS:
            self.assertIn(key, server.POS_RESOURCE_MODULES)
            self.assertTrue(server.POS_ADMIN_RESOURCES[key]["read_only"])
            self.assertEqual(server.POS_BRIDGE_RESOURCES[key]["pos_resource"], key)
            with self.assertRaises(HTTPException) as caught:
                server.require_pos_resource_writable(server.POS_ADMIN_RESOURCES[key])
            self.assertEqual(caught.exception.status_code, 409)

    async def test_unauthorized_business_is_rejected_before_querying_records(self):
        with patch.multiple(server, get_current_user=AsyncMock(return_value={"role": "staff", "business_ids": ["allowed"]}),
                            require_module_for_business_scope=AsyncMock()):
            with self.assertRaises(HTTPException) as caught:
                await server.list_pos_admin_records("payroll", None, business_id="foreign")
        self.assertEqual(caught.exception.status_code, 403)

    async def test_tenant_reader_still_requires_module_entitlement(self):
        guard = AsyncMock(side_effect=HTTPException(403, "Module disabled"))
        with patch.multiple(server, get_current_user=AsyncMock(return_value={"role": "manager", "business_ids": ["b"]}),
                            require_module_for_business_scope=guard):
            with self.assertRaises(HTTPException) as caught:
                await server.list_pos_admin_records("customer-profiles", None, business_id="b")
        self.assertEqual(caught.exception.status_code, 403)
        guard.assert_awaited_once()

    async def test_source_metadata_and_source_dates_survive_import(self):
        collection = SimpleNamespace(find_one=AsyncMock(return_value=None), insert_one=AsyncMock())
        row = {"id": "card", "business_id": "pos-b", "tenant_id": "tenant", "balance": 0,
               "masked_code": "•••• 1234", "created_at": "2026-01-01T12:00:00Z"}
        with patch.multiple(server, db={"pos_gift_cards": collection}, assert_pos_row_scope=AsyncMock(),
                            local_outlet_id_for_pos_row=AsyncMock(return_value="")):
            with patch.object(server, 'projection_privacy', return_value=SimpleNamespace(sanitize=AsyncMock(return_value=row))):
                await server.persist_bridge_pos_record(server.POS_BRIDGE_RESOURCES["gift-cards"], row, "local-b", {"id": "admin"}, "now")
        saved = collection.insert_one.call_args.args[0]
        self.assertEqual(saved["metadata"]["balance"], 0)
        self.assertEqual(saved["metadata"]["masked_code"], "•••• 1234")
        self.assertEqual(saved["created_at"], row["created_at"])

    async def test_list_uses_business_filter_and_reports_pagination(self):
        cursor = Mock()
        cursor.sort.return_value = cursor; cursor.skip.return_value = cursor
        cursor.to_list = AsyncMock(return_value=[{"id": "r", "business_id": "b"}])
        collection = SimpleNamespace(find=Mock(return_value=cursor), count_documents=AsyncMock(return_value=52))
        with patch.multiple(server, db={"pos_gift_cards": collection},
                            get_current_user=AsyncMock(return_value={"role": "platform_admin"}),
                            require_module_for_business_scope=AsyncMock(),
                            pos_business_filter=AsyncMock(return_value={"business_id": "b"}),
                            summarize_pos_collection=AsyncMock(return_value={"total": 52, "amount_total": 0, "status_counts": []}),
                            is_pos_connected_business=AsyncMock(return_value=False), decorate_pos_records=AsyncMock(side_effect=lambda rows: rows)):
            result = await server.list_pos_admin_records("gift-cards", None, business_id="b", limit=50, offset=50,
                outlet_id=None, status=None, search=None, date_from=None, date_to=None)
        self.assertEqual(collection.find.call_args.args[0]["business_id"], "b")
        cursor.skip.assert_called_once_with(50)
        self.assertEqual(result["pagination"], {"limit": 50, "offset": 50, "total": 52, "has_more": True})
