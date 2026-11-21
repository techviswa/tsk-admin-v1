import os
import unittest
from uuid import uuid4
from motor.motor_asyncio import AsyncIOMotorClient
from pos_membership_reconciliation import reconcile_staff_memberships


@unittest.skipUnless(os.environ.get('RUN_PROJECTION_DB_TESTS') == '1', 'Isolated MongoDB acceptance enabled explicitly')
class MembershipReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_snapshot_resolves_only_older_failed_imports_and_retains_diagnostics(self):
        from pos_reconciliation import reconcile_import_failures
        client = AsyncIOMotorClient('mongodb://127.0.0.1:27017', serverSelectionTimeoutMS=5000)
        name = 'membership_acceptance_' + uuid4().hex
        jobs = client[name].pos_change_jobs
        try:
            baseline = {'business_id': 'a', 'resource': 'orders', 'status': 'failed', 'created_at': '2026-10-01', 'last_error': 'Retained diagnostic', 'attempts': 5}
            await jobs.insert_many([{**baseline, '_id': 'old'}, {**baseline, '_id': 'new', 'created_at': '2026-10-03'},
                {**baseline, '_id': 'other', 'business_id': 'b'}, {**baseline, '_id': 'running', 'status': 'running'}])
            await reconcile_import_failures(jobs, 'a', 'orders', '2026-10-02', {'business_id': 'source', 'tenant_id': 'tenant'})
            old = await jobs.find_one({'_id': 'old'})
            self.assertEqual(old['status'], 'reconciled')
            self.assertEqual(old['last_error'], 'Retained diagnostic')
            self.assertEqual(old['attempts'], 5)
            self.assertEqual(await jobs.count_documents({'status': 'failed'}), 2)
            self.assertEqual((await jobs.find_one({'_id': 'running'}))['status'], 'running')
        finally:
            await client.drop_database(name)
            client.close()

    async def test_missing_source_staff_loses_only_its_membership_and_keeps_history(self):
        client = AsyncIOMotorClient('mongodb://127.0.0.1:27017', serverSelectionTimeoutMS=5000)
        name = 'membership_acceptance_' + uuid4().hex
        users = client[name].users
        scope = {'business_id': 'source', 'tenant_id': 'tenant'}
        link = {**scope, 'user_id': 'gone', 'status': 'active', 'last_seen_at': '2026-10-01'}
        try:
            await users.insert_many([
                {'id': 'single', 'status': 'active', 'business_ids': ['a'], 'updated_at': '2026-10-01', 'pos_links': {'a': link}},
                {'id': 'shared', 'status': 'active', 'business_ids': ['a', 'b'], 'updated_at': '2026-10-01', 'pos_links': {'a': link, 'b': {'status': 'active'}}},
                {'id': 'newer', 'status': 'active', 'business_ids': ['a'], 'updated_at': '2026-10-03', 'pos_links': {'a': {**link, 'last_seen_at': '2026-10-03'}}},
                {'id': 'present', 'status': 'active', 'business_ids': ['a'], 'updated_at': '2026-10-01', 'pos_links': {'a': {**link, 'user_id': 'present'}}},
            ])
            await reconcile_staff_memberships(users, 'a', scope, ['present'], '2026-10-02')
            self.assertEqual((await users.find_one({'id': 'single'}))['status'], 'inactive')
            shared = await users.find_one({'id': 'shared'})
            self.assertEqual(shared['status'], 'active')
            self.assertEqual(shared['pos_links']['a']['status'], 'inactive')
            self.assertEqual(shared['pos_links']['b']['status'], 'active')
            self.assertEqual(shared['token_version'], 1)
            for identity in ['newer', 'present']:
                self.assertNotIn('token_version', await users.find_one({'id': identity}))
            self.assertEqual(await users.count_documents({}), 4)
        finally:
            await client.drop_database(name)
            client.close()
