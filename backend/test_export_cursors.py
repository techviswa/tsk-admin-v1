import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from fastapi import HTTPException

with patch.dict(os.environ, {'MONGO_URL': 'mongodb://127.0.0.1:27017', 'DB_NAME': 'export_cursor_unit'}):
    import server


def page(identifier, offset=0, more=False, complete=True, strategy='bounded-id-v1'):
    return {'resource': 'attendance', 'business_id': 'pos-b', 'tenant_id': 'tenant', 'items': [{'id': identifier}],
            'meta': {'pagination': {'offset': offset, 'limit': 1, 'next_offset': offset + 1,
                'has_more': more, 'complete_scope': complete, 'strategy': strategy, 'next_cursor': 'signed-next-page' if more else None}}}


class ExportCursorTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, pages, params=None):
        responses = [SimpleNamespace(status_code=200, json=Mock(return_value=value), raise_for_status=Mock()) for value in pages]
        self.send = Mock(side_effect=responses)
        with patch.multiple(server, POS_CORE_API_BASE_URL='http://pos.test', POS_CORE_API_KEY='unit-key',
                            pos_headers_for_admin_business=AsyncMock(return_value={'business_id': 'pos-b', 'x-tenant-id': 'tenant'}),
                            raise_if_pos_rate_limited=Mock(), send_pos_request=self.send,
                            prepare_pos_bridge_rows=AsyncMock(side_effect=lambda resource, payload, business: payload['items']),
                            validate_pos_rows_for_business=AsyncMock()):
            return await server.pos_bridge_request('attendance', params or {'limit': 1}, business_id='b')

    async def test_each_page_forwards_the_source_continuation(self):
        result = await self.fetch([page('first', more=True), page('second', offset=1)])
        self.assertEqual(self.send.call_args_list[1].kwargs['params']['cursor'], 'signed-next-page')
        self.assertEqual([row['id'] for row in result['items']], ['first', 'second'])
        self.assertTrue(result['meta']['complete_snapshot'])
        self.assertTrue(result['meta']['pagination']['complete_scope'])

    async def test_midstream_import_cannot_claim_a_complete_business_snapshot(self):
        result = await self.fetch([page('second', offset=1)], {'limit': 1, 'offset': 1, 'cursor': 'already-started'})
        self.assertFalse(result['meta']['complete_snapshot'])
        self.assertFalse(result['meta']['pagination']['complete_scope'])

    async def test_filtered_export_never_infers_other_records_are_missing(self):
        result = await self.fetch([page('active', complete=False)], {'limit': 1, 'status': 'active'})
        with patch.object(server, 'expected_pos_scope_for_business', AsyncMock()) as scope:
            await server.reconcile_complete_pos_snapshot('attendance', {}, result, [], [], 'b', 'now')
        scope.assert_not_awaited()

    async def test_export_strategy_cannot_change_between_pages(self):
        with self.assertRaises(HTTPException) as caught:
            await self.fetch([page('first', more=True), page('second', offset=1, strategy='legacy-offset')])
        self.assertEqual(caught.exception.status_code, 502)

    async def test_missing_continuation_rejects_the_export_before_import(self):
        first = page('first', more=True)
        first['meta']['pagination']['next_cursor'] = None
        with self.assertRaises(HTTPException) as caught:
            await self.fetch([first])
        self.assertEqual(caught.exception.status_code, 502)

    async def test_unfiltered_complete_cursor_export_can_reconcile_absent_rows(self):
        result = await self.fetch([page('present')])
        reconcile = AsyncMock()
        with patch.multiple(server, db={'records': object(), 'pos_change_jobs': SimpleNamespace(update_many=AsyncMock())}, expected_pos_scope_for_business=AsyncMock(return_value={'business_id': 'pos-b', 'tenant_id': 'tenant'}),
                            reconcile_snapshot=reconcile):
            await server.reconcile_complete_pos_snapshot('attendance', {'collection': 'records'}, result, [{'external_id': 'present'}], [], 'b', 'now')
            reconcile.assert_awaited_once()
