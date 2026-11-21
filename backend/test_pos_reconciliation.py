import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pymongo.errors import DuplicateKeyError
from pos_reconciliation import PosReconciler, import_lease
from fastapi import HTTPException


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_one_importer_can_own_a_resource(self):
        collection = SimpleNamespace(find_one_and_update=AsyncMock(side_effect=DuplicateKeyError('held')))
        with self.assertRaises(HTTPException) as caught:
            async with import_lease(collection, 'b', 'products'):
                self.fail('A competing importer acquired the lease')
        self.assertEqual(caught.exception.status_code, 409)

    async def test_lease_is_released_by_owner_after_import_failure(self):
        async def acquire(query, update, **kwargs):
            return {'owner': update['$set']['owner']}
        collection = SimpleNamespace(find_one_and_update=AsyncMock(side_effect=acquire), update_one=AsyncMock())
        with self.assertRaisesRegex(RuntimeError, 'import failed'):
            async with import_lease(collection, 'b', 'products'):
                raise RuntimeError('import failed')
        self.assertEqual(collection.update_one.call_args.args[0]['_id'], 'import:b:products')
        self.assertIn('owner', collection.update_one.call_args.args[0])

    async def test_background_schedule_queues_resources_without_any_browser(self):
        schedules = SimpleNamespace(find_one_and_update=AsyncMock(return_value={'_id': 'b'}), update_one=AsyncMock())
        db = SimpleNamespace(pos_reconcile_schedule=schedules, businesses=SimpleNamespace(find_one=AsyncMock(return_value={'id': 'b', 'pos_external_id': 'source', 'pos_tenant_id': 'tenant'})))
        worker = SimpleNamespace(enqueue_snapshot=AsyncMock())
        self.assertTrue(await PosReconciler(db, worker, ['products', 'attendance'], interval=300).run_once())
        self.assertEqual(worker.enqueue_snapshot.await_count, 2)
        worker.enqueue_snapshot.assert_any_await('attendance', 'b', fresh_for_seconds=300)
        self.assertIn('last_scheduled_at', schedules.update_one.call_args.args[1]['$set'])

    async def test_deleted_business_is_not_recreated_by_schedule(self):
        schedules = SimpleNamespace(find_one_and_update=AsyncMock(return_value={'_id': 'removed'}), update_one=AsyncMock())
        worker = SimpleNamespace(enqueue_snapshot=AsyncMock())
        db = SimpleNamespace(pos_reconcile_schedule=schedules, businesses=SimpleNamespace(find_one=AsyncMock(return_value=None)))
        await PosReconciler(db, worker, ['products']).run_once()
        worker.enqueue_snapshot.assert_not_awaited()
