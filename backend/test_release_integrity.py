import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from decimal import Decimal
from fastapi import HTTPException
from pos_projection import require_local_write, money, bill_revenue, report_filter

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "release_tests"}):
    import server


class ReleaseIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def test_pos_owned_records_cannot_be_modified_as_local_documents(self):
        for arguments in [{"record": {"pos_synced": True}}, {"record": {"pos_external_id": "p"}}, {"connected": True}]:
            with self.assertRaises(HTTPException) as caught:
                require_local_write({}, **arguments)
            self.assertEqual(caught.exception.status_code, 409)
        require_local_write({}, {"id": "local"})

    def test_currency_rounding_uses_decimal_half_up(self):
        self.assertEqual(money(Decimal("1.005")), 1.01)
        self.assertEqual(money(Decimal("-1.005")), -1.01)

    def test_invoice_refunds_and_date_boundaries(self):
        cases = [("paid", "100.10", "25.05", "75.05"), ("issued", "10", "20", "0"),
                 ("void", "10", "0", "0"), ("paid", "0", "0", "0")]
        for status, total, refund, expected in cases:
            self.assertEqual(bill_revenue({'metadata': {'status': status, 'total': total, 'refunded_amount': refund}}), Decimal(expected))
        for value in ['NaN', 'Infinity', 'wrong']:
            with self.assertRaises(HTTPException):
                bill_revenue({'metadata': {'total': value}})
        self.assertEqual(report_filter({}, date_from='2026-10-01', date_to='2026-10-31')['created_at'],
                         {'$gte': '2026-10-01T00:00:00', '$lt': '2026-11-01T00:00:00'})

    async def test_product_and_outlet_imports_never_match_editable_names(self):
        for resource, function, default in [("products", server.sync_bridge_product, "Same name"), ("outlets", server.sync_bridge_outlet, "Same name")]:
            with self.subTest(resource=resource):
                collection = SimpleNamespace(find_one=AsyncMock(return_value=None), insert_one=AsyncMock())
                with patch.multiple(server, db=SimpleNamespace(**{resource: collection}), assert_pos_row_scope=AsyncMock()):
                    await function({"id": "new-source", "name": default, "code": "SAME"}, "business", "now")
                query = collection.find_one.call_args.args[0]
                self.assertEqual(query["business_id"], "business")
                self.assertEqual(query["$or"], [{"pos_external_id": "new-source"}, {"external_id": "new-source"}])
                self.assertEqual(collection.insert_one.call_args.args[0]["pos_external_id"], "new-source")


@unittest.skipUnless(os.environ.get("RUN_PROJECTION_DB_TESTS") == "1", "Explicit isolated MongoDB acceptance check")
class ProjectionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_updates_canonical_record_without_reviving_archived_duplicate(self):
        for resource, importer in [('products', server.sync_bridge_product), ('outlets', server.sync_bridge_outlet)]:
            collection = self.database[resource]
            await collection.insert_many([
                {'id': 'alias', 'business_id': 'local', 'pos_external_id': 'source', 'name': 'Archived duplicate', 'source_alias_to': 'canonical', 'deleted_at': 'archived'},
                {'id': 'canonical', 'business_id': 'local', 'pos_external_id': 'source', 'name': 'Before import'},
            ])
            with patch.object(server, 'db', self.database), patch.object(server, 'assert_pos_row_scope', AsyncMock()):
                result = await importer({'id': 'source', 'business_id': 'pos-local', 'tenant_id': 'tenant', 'name': 'Confirmed source', 'price': 10, 'stock': 5}, 'local', 'now')
            self.assertEqual(result, 'canonical')
            self.assertEqual((await collection.find_one({'id': 'canonical'}))['name'], 'Confirmed source')
            self.assertEqual((await collection.find_one({'id': 'alias'}))['name'], 'Archived duplicate')
            self.assertEqual(await collection.count_documents({}), 2)

    async def test_concurrent_audit_replay_records_one_operation(self):
        import asyncio
        with patch.object(server, 'db', self.database):
            await asyncio.gather(*(server.create_audit_log('a', 'actor', 'actor@example.test', 'updated', 'outlet', 'o',
                {'pos_confirmed': True}, operation_id='same-command') for _ in range(20)))
        self.assertEqual(await self.database.audit_logs.count_documents({'entity_id': 'o'}), 1)

    async def asyncSetUp(self):
        from motor.motor_asyncio import AsyncIOMotorClient
        from uuid import uuid4
        url = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
        self.client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
        self.database = self.client["admincore_projection_check_" + uuid4().hex]
        self.collection = self.database.records

    async def asyncTearDown(self):
        self.assertTrue(self.database.name.startswith("admincore_projection_check_"))
        await self.client.drop_database(self.database.name)
        self.client.close()

    async def test_complete_scoped_decimal_totals_and_mixed_currency_guard(self):
        from pos_projection import summarize
        await self.collection.insert_many([{"business_id": "a", "amount": "0.10", "currency": "INR", "status": "paid"} for _ in range(1501)] +
                                          [{"business_id": "b", "amount": "999999", "currency": "INR"}])
        result = await summarize(self.collection, {"business_id": "a"})
        self.assertEqual(result["total"], 1501)
        self.assertEqual(result["amount_total"], 150.10)
        await self.collection.insert_one({"business_id": "a", "amount": "2", "currency": "USD"})
        result = await summarize(self.collection, {"business_id": "a"})
        self.assertIsNone(result["amount_total"])
        self.assertEqual({row["currency"] for row in result["amount_by_currency"]}, {"INR", "USD"})
        await self.collection.insert_one({"business_id": "a", "amount": "invalid", "currency": "USD"})
        result = await summarize(self.collection, {"business_id": "a"})
        self.assertEqual(result["invalid_amounts"], 1)

    async def test_invoice_aggregation_matches_refund_projection_and_rejects_nonfinite(self):
        from pos_projection import summarize, bill_revenue_expression
        rows = [{'amount': '100.10', 'metadata': {'total': '100.10', 'refunded_amount': '25.05'}},
                {'amount': '10', 'metadata': {'total': '10', 'metadata': {'refunded_amount': '20'}}}]
        await self.collection.insert_many(rows)
        result = await summarize(self.collection, {}, bill_revenue_expression())
        self.assertEqual(result['amount_total'], 75.05)
        self.assertEqual(result['positive'], 1)
        for value in ['NaN', 'Infinity', '-Infinity', 'wrong']:
            await self.collection.insert_one({'metadata': {'total': '12', 'refunded_amount': value}})
        result = await summarize(self.collection, {}, bill_revenue_expression())
        self.assertEqual(result['invalid_amounts'], 4)
        self.assertIsNone(result['amount_total'])
