import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from test_pos_product_commands import lease

with patch.dict(os.environ, {'MONGO_URL': 'mongodb://127.0.0.1:27017', 'DB_NAME': 'source_review_test'}):
    import server


class Database(dict):
    __getattr__ = dict.__getitem__


class SourceMappingReviewTests(unittest.IsolatedAsyncioTestCase):
    async def review(self, *, active=False, canonical=None, archive=False, matched=1, source_id='source'):
        self.record = {'id': 'legacy', 'business_id': 'b', 'name': 'Same name', 'pos_sync_pending': True}
        if active:
            self.record['pos_command'] = {'id': 'live-command', 'version': 1}
        async def find(query, *args):
            return canonical if isinstance(query.get('id'), dict) else deepcopy(self.record)
        self.collection = SimpleNamespace(find_one=AsyncMock(side_effect=find), update_one=AsyncMock(return_value=SimpleNamespace(matched_count=matched)))
        self.proxy = AsyncMock(return_value={'items': [{'id': 'source', 'business_id': 'pos-b', 'tenant_id': 'tenant', 'name': 'Same name', 'updated_at': '2026-10-28T00:00:00Z'}]})
        self.audit = AsyncMock()
        database = Database(outlets=self.collection, products=self.collection, pos_import_leases=None)
        with patch.multiple(server, db=database, validate_business_access=AsyncMock(), import_lease=lease,
                            pos_bridge_request=self.proxy, assert_pos_row_scope=AsyncMock(), create_audit_log=self.audit):
            return await server.link_pos_record_source('outlets', 'legacy', server.ProductSourceLink(source_id=source_id, reason='Reviewed exact source ID', archive_duplicate=archive),
                                                       {'id': 'admin', 'email': 'admin@example.test', 'role': 'platform_admin'})

    async def test_pending_durable_command_cannot_be_remapped(self):
        with self.assertRaises(HTTPException) as caught:
            await self.review(active=True)
        self.assertEqual(caught.exception.status_code, 409)
        self.proxy.assert_not_awaited()
        self.collection.update_one.assert_not_awaited()

    async def test_names_do_not_match_a_different_source_id(self):
        with self.assertRaises(HTTPException) as caught:
            await self.review(source_id='different-source')
        self.assertEqual(caught.exception.status_code, 404)
        self.collection.update_one.assert_not_awaited()

    async def test_explicit_duplicate_review_retains_history_and_canonical_identity(self):
        canonical = {'id': 'canonical', 'pos_external_id': 'source'}
        with self.assertRaises(HTTPException) as caught:
            await self.review(canonical=canonical)
        self.assertEqual(caught.exception.detail['code'], 'POS_SOURCE_ALREADY_LINKED')
        self.collection.update_one.assert_not_awaited()
        result = await self.review(canonical=canonical, archive=True)
        self.assertEqual(result['canonical_id'], 'canonical')
        changes = self.collection.update_one.call_args.args[1]['$set']
        self.assertTrue(changes['deleted_at'])
        self.assertEqual(changes['source_alias_to'], 'canonical')

    async def test_new_intent_during_review_rejects_stale_mapping_write(self):
        with self.assertRaises(HTTPException) as caught:
            await self.review(matched=0)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.collection.update_one.call_args.args[0]['pos_sync_pending'], True)
        self.assertIn('pos_command', self.collection.update_one.call_args.args[0])
        self.audit.assert_not_awaited()

    async def test_archived_canonical_mapping_cannot_be_silently_reactivated(self):
        with self.assertRaises(HTTPException) as caught:
            await self.review(canonical={'id': 'canonical', 'deleted_at': 'archived'}, archive=True)
        self.assertEqual(caught.exception.status_code, 409)
        self.collection.update_one.assert_not_awaited()
