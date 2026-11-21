import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from pos_entitlements import pos_entitlement_payload

with patch.dict(os.environ, {'MONGO_URL': 'mongodb://127.0.0.1:27017', 'DB_NAME': 'fulfillment_policy_tests'}):
    import server


class FulfillmentPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_tenant_cannot_change_platform_billing_policy(self):
        with patch.object(server, 'get_current_user', AsyncMock(return_value={'role': 'business_owner'})), \
             patch.object(server, 'validate_business_access', AsyncMock()) as access:
            with self.assertRaises(HTTPException) as caught:
                await server.update_fulfillment_modes('business', None)
            self.assertEqual(caught.exception.status_code, 403)
            access.assert_not_awaited()

    async def test_versioned_policy_is_saved_and_queued_without_reporting_pos_success(self):
        modes = {'TABLE': False, 'TOKEN': True, 'PICKUP': False, 'DELIVERY': True}
        business = {'id': 'business', 'pos_external_id': 'source', 'pos_tenant_id': 'tenant', 'fulfillment_modes': modes, 'fulfillment_modes_version': 1}
        collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)))
        worker = SimpleNamespace(enqueue_snapshot=AsyncMock())
        with patch.multiple(server, db=SimpleNamespace(businesses=collection), pos_sync_worker=worker,
                            get_current_user=AsyncMock(return_value={'id': 'admin', 'email': 'admin@test'}),
                            require_platform_permission=lambda *args: None,
                            validate_business_access=AsyncMock(return_value=business), create_audit_log=AsyncMock()):
            result = await server.update_fulfillment_modes('business', SimpleNamespace(json=AsyncMock(return_value={'modes': modes, 'version': 0})))
        self.assertEqual(result['sync_status'], 'pending')
        self.assertEqual(collection.update_one.call_args.args[1]['$set']['fulfillment_modes'], modes)
        worker.enqueue_snapshot.assert_awaited_once_with('tenant-entitlements', 'business', priority=100)
        self.assertEqual(pos_entitlement_payload({'fulfillment_modes': modes})['fulfillment_modes'], modes)

    async def test_invalid_policy_or_stale_version_cannot_overwrite(self):
        valid = dict.fromkeys(server.FULFILLMENT_MODES, True)
        collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(matched_count=0)))
        worker = SimpleNamespace(enqueue_snapshot=AsyncMock())
        with patch.multiple(server, db=SimpleNamespace(businesses=collection), pos_sync_worker=worker,
                            get_current_user=AsyncMock(return_value={}), require_platform_permission=lambda *args: None,
                            validate_business_access=AsyncMock(return_value={'pos_external_id': 'source', 'pos_tenant_id': 'tenant'})):
            for modes, version, status in [({**valid, 'TOKEN': 'false'}, 0, 422), (valid, True, 422), (valid, 0, 409)]:
                with self.assertRaises(HTTPException) as caught:
                    await server.update_fulfillment_modes('business', SimpleNamespace(json=AsyncMock(return_value={'modes': modes, 'version': version})))
                self.assertEqual(caught.exception.status_code, status)
        worker.enqueue_snapshot.assert_not_awaited()

    def test_legacy_entitlements_do_not_reset_existing_pos_configuration(self):
        self.assertNotIn('fulfillment_modes', pos_entitlement_payload({}))
