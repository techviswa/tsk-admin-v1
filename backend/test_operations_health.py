import os
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from fastapi import HTTPException
from operations_health import queue_health, QUEUES

with patch.dict(os.environ, {'MONGO_URL': 'mongodb://127.0.0.1:27017', 'DB_NAME': 'operations_health_unit'}):
    import server


class OperationsHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_and_staleness_are_reported_without_payloads(self):
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=[{'_id': 'failed', 'count': 1}]))
        collection = SimpleNamespace(aggregate=Mock(return_value=cursor), count_documents=AsyncMock(return_value=2))
        result = await queue_health({name: collection for name in QUEUES})
        self.assertFalse(result['healthy'])
        self.assertEqual(result['queues']['pos_profile_jobs']['stale_active'], 2)
        self.assertNotIn('payload', json.dumps(result))

    async def test_tenant_cannot_inspect_operator_health(self):
        with patch.object(server, 'get_current_user', AsyncMock(return_value={'role': 'business_owner'})):
            with self.assertRaises(HTTPException) as caught:
                await server.operations_health(None)
        self.assertEqual(caught.exception.status_code, 403)

    async def test_missing_bridge_capability_is_not_reported_healthy(self):
        with patch.multiple(server, get_current_user=AsyncMock(return_value={'role': 'platform_admin'}),
                            queue_health=AsyncMock(return_value={'healthy': True, 'queues': {}}),
                            POS_CORE_API_BASE_URL='http://pos.test',
                            pos_core_session_request=AsyncMock(return_value={'data': {'product_commands': 1}})):
            response = await server.operations_health(None)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(json.loads(response.body)['healthy'])
