import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

with patch.dict(os.environ, {'MONGO_URL': 'mongodb://127.0.0.1:27017', 'DB_NAME': 'admin_console_read_tests'}):
    import server


class AdminConsoleReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_platform_bridge_read_uses_explicit_permission_not_tenant_entitlement(self):
        with patch.object(server, 'require_platform_permission') as permission, patch.object(server, 'require_module_for_business_scope', AsyncMock()) as gate:
            await server.require_pos_bridge_access({'role': 'platform_admin'}, 'business')
            permission.assert_called_once_with({'role': 'platform_admin'}, 'pos.read')
            gate.assert_not_awaited()

    async def test_generic_entitlement_queue_requires_module_write_permission(self):
        with patch.object(server, 'get_pos_bridge_request_user', AsyncMock(return_value={'role': 'platform_support'})), \
             patch.object(server, 'require_platform_permission', side_effect=HTTPException(403, 'Denied')) as permission:
            with self.assertRaises(HTTPException):
                await server.queue_pos_bridge_resource('tenant-entitlements', None, business_id='business')
            permission.assert_called_once_with({'role': 'platform_support'}, 'modules.write')

    async def test_client_search_treats_regular_punctuation_as_literal_text(self):
        cursor = MagicMock(); cursor.sort.return_value = cursor; cursor.to_list = AsyncMock(return_value=[])
        clients = SimpleNamespace(find=MagicMock(return_value=cursor))
        with patch.multiple(server, db=SimpleNamespace(clients=clients), get_current_user=AsyncMock(return_value={'role': 'platform_admin'})):
            await server.list_clients(None, business_id=None, status=None, search='A+B(')
        self.assertEqual(clients.find.call_args.args[0]['$or'][0]['owner_name']['$regex'], r'A\+B\(')

    async def test_settings_and_integration_reads_keep_platform_scope_without_tenant_module(self):
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=[]))
        database = SimpleNamespace(settings=SimpleNamespace(find=MagicMock(return_value=cursor)), integrations=SimpleNamespace(find=MagicMock(return_value=cursor)))
        with patch.multiple(server, db=database, get_current_user=AsyncMock(return_value={'role': 'platform_admin'}),
                            require_business_admin_scope=AsyncMock(return_value={'owner_id': 'owner'})), \
             patch.object(server, 'require_business_module_enabled', AsyncMock(side_effect=HTTPException(403, 'Module disabled'))) as gate:
            self.assertEqual(await server.list_settings('business', None, category=None), [])
            self.assertEqual(await server.list_integrations('business', None), [])
            gate.assert_not_awaited()

    async def test_explicit_missing_preference_is_created_with_stable_scoped_identity(self):
        settings = SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock())
        with patch.multiple(server, db=SimpleNamespace(settings=settings), get_current_user=AsyncMock(return_value={'role': 'platform_admin', 'id': 'admin', 'email': 'admin@test'}),
                            require_business_admin_scope=AsyncMock(), create_audit_log=AsyncMock()):
            await server.update_setting('business', 'email_notifications', server.SettingUpdate(value='false'), None)
        query, update = settings.update_one.call_args.args
        self.assertEqual(query, {'_id': 'setting:business:email_notifications'})
        self.assertEqual(update['$set']['value'], 'false')
        self.assertEqual(update['$setOnInsert']['business_id'], 'business')
        self.assertEqual(update['$setOnInsert']['category'], 'notifications')

    async def test_invoice_import_preserves_zero_and_official_reference(self):
        row = {'id': 'source-bill', 'total': 0, 'amount': 99, 'invoice_number': 'INV-1', 'customer_name': 'Guest'}
        collection = SimpleNamespace(find_one=AsyncMock(return_value=None), insert_one=AsyncMock())
        with patch.multiple(server, db={'pos_bills': collection}, assert_pos_row_scope=AsyncMock(), local_outlet_id_for_pos_row=AsyncMock(return_value='outlet')), \
             patch.object(server, 'projection_privacy', return_value=SimpleNamespace(sanitize=AsyncMock(return_value=row))):
            await server.persist_bridge_pos_record({'collection': 'pos_bills', 'label': 'Bills', 'pos_resource': 'bills'}, row, 'business', {'id': 'admin'}, 'now')
        saved = collection.insert_one.call_args.args[0]
        self.assertEqual(saved['amount'], 0)
        self.assertEqual(saved['title'], 'INV-1')

    async def test_platform_audit_keeps_permission_scope_but_does_not_require_tenant_module(self):
        cursor = MagicMock()
        for name in ['sort', 'skip', 'limit']: getattr(cursor, name).return_value = cursor
        cursor.to_list = AsyncMock(return_value=[{'id': 'event', 'entity_type': 'user'}])
        collection = SimpleNamespace(find=MagicMock(return_value=cursor), count_documents=AsyncMock(return_value=25))
        with patch.multiple(server, db=SimpleNamespace(audit_logs=collection),
             get_current_user=AsyncMock(return_value={'role': 'platform_admin'}),
             require_business_admin_scope=AsyncMock()) as patches, \
             patch.object(server, 'require_business_module_enabled', AsyncMock(side_effect=HTTPException(403, 'Module disabled'))) as gate:
            result = await server.list_audit_logs('business', None, limit=20, skip=20, entity_type='user')
            gate.assert_not_awaited()
        query = {'business_id': 'business', 'entity_type': 'user'}
        collection.find.assert_called_once_with(query, {'_id': 0})
        collection.count_documents.assert_awaited_once_with(query)
        self.assertEqual(result['total'], 25)

    async def test_tenant_still_requires_audit_module(self):
        with patch.multiple(server, get_current_user=AsyncMock(return_value={'role': 'business_owner'}),
                            require_business_admin_scope=AsyncMock(),
                            require_business_module_enabled=AsyncMock(side_effect=HTTPException(403, 'Module disabled'))):
            with self.assertRaises(HTTPException):
                await server.list_audit_logs('business', None, limit=20, skip=0)

    async def test_platform_flags_require_permission_scope_without_tenant_module(self):
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=[]))
        with patch.multiple(server, get_current_user=AsyncMock(return_value={'role': 'platform_admin'}),
                            db=SimpleNamespace(feature_flags=SimpleNamespace(find=MagicMock(return_value=cursor)))), \
             patch.object(server, 'require_business_admin_scope', AsyncMock()) as access, \
             patch.object(server, 'require_business_module_enabled', AsyncMock(side_effect=HTTPException(403, 'Module disabled'))) as gate:
            self.assertEqual(await server.list_feature_flags('business', None), [])
            access.assert_awaited_once_with({'role': 'platform_admin'}, 'business', permission='businesses.read')
            gate.assert_not_awaited()
