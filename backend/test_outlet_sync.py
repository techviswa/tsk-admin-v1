import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pos_product_commands import ProductCommands
from test_pos_product_commands import lease

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "outlet_tests"}):
    import server


class OutletSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_filter_is_applied_before_pagination_and_count(self):
        from fastapi import Response
        for status in ('active', 'inactive', 'archived', 'all'):
            cursor = MagicMock()
            cursor.sort.return_value = cursor
            cursor.skip.return_value = cursor
            cursor.to_list = AsyncMock(return_value=[])
            outlets = SimpleNamespace(find=MagicMock(return_value=cursor), count_documents=AsyncMock(return_value=0))
            response = Response()
            with patch.multiple(server, db=SimpleNamespace(outlets=outlets),
                                get_current_user=AsyncMock(return_value={'role': 'platform_admin'}),
                                validate_business_access=AsyncMock(), require_business_module_enabled=AsyncMock(),
                                is_pos_connected_business=AsyncMock(return_value=False)):
                await server.list_outlets('business', None, response, limit=50, offset=50, status=status)
            query = outlets.find.call_args.args[0]
            self.assertEqual(query['business_id'], 'business')
            self.assertEqual(query.get('status'), None if status == 'all' else status)
            outlets.count_documents.assert_awaited_once_with(query)
            cursor.skip.assert_called_once_with(50)

    async def test_updates_and_deletes_queue_without_changing_confirmed_fields(self):
        for operation in ("update", "delete"):
            with self.subTest(operation=operation):
                outlet = {"id": "o", "business_id": "b", "pos_external_id": "source", "pos_metadata": {"updated_at": "version-1"}, "name": "Original", "status": "active"}
                outlets = SimpleNamespace(find_one=AsyncMock(return_value=outlet), update_one=AsyncMock(), delete_one=AsyncMock())
                queue = AsyncMock(return_value={**outlet, "pos_sync_pending": True})
                with patch.multiple(server, db=SimpleNamespace(outlets=outlets), POS_CORE_API_BASE_URL="http://127.0.0.1:4001",
                                    get_current_user=AsyncMock(return_value={"role": "platform_admin"}),
                                    require_business_management=AsyncMock(), require_business_module_enabled=AsyncMock()), \
                     patch.object(server.pos_outlet_commands, "enqueue", queue):
                    result = await server.update_outlet("o", server.OutletUpdate(name="Changed", expected_updated_at="version-1"), None) if operation == "update" else await server.delete_outlet("o", None, expected_updated_at="version-1")
                self.assertEqual(result.status_code, 202)
                self.assertEqual(queue.call_args.args[1], operation)
                self.assertEqual(outlet["name"], "Original")
                outlets.update_one.assert_not_awaited()
                outlets.delete_one.assert_not_awaited()

    def test_client_form_version_must_match_the_confirmed_source_version(self):
        record = {"pos_external_id": "source", "pos_metadata": {"updated_at": "version-2"}}
        with patch.object(server, "POS_CORE_API_BASE_URL", "http://127.0.0.1:4001"):
            for version in [None, "version-1"]:
                with self.assertRaises(HTTPException) as caught:
                    server.require_source_edit_version(record, version)
                self.assertEqual(caught.exception.detail["code"], "POS_SOURCE_VERSION_CONFLICT")
            server.require_source_edit_version(record, "version-2")

    async def test_deleted_pos_outlet_is_not_recreated_by_snapshot(self):
        outlet = {"id": "o", "business_id": "b", "pos_external_id": "pos-o", "deleted_at": "confirmed-archive"}
        outlets = SimpleNamespace(find_one=AsyncMock(return_value=outlet), update_one=AsyncMock(), insert_one=AsyncMock())
        with patch.multiple(server, db=SimpleNamespace(outlets=outlets), assert_pos_row_scope=AsyncMock()):
            self.assertEqual(await server.sync_bridge_outlet({"id": "pos-o", "name": "Retained"}, "b", "now"), "o")
        outlets.update_one.assert_not_awaited()
        outlets.insert_one.assert_not_awaited()


class OutletCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.outlet = {"id": "o", "business_id": "b", "pos_external_id": "source", "name": "Original", "status": "active", "pos_sync_pending": True,
                       "pos_command": {"id": "command", "version": 1, "operation": "update", "changes": {"name": "New"}, "actor_id": "a", "actor_email": "a@example.test"}}
        async def update(query, changes):
            for key, value in changes.get("$set", {}).items():
                if key.startswith("pos_command."):
                    self.outlet["pos_command"][key.split(".", 1)[1]] = value
                else:
                    self.outlet[key] = value
        self.collection = SimpleNamespace(find_one=AsyncMock(side_effect=lambda *a, **k: deepcopy(self.outlet)), update_one=AsyncMock(side_effect=update))
        self.push, self.delete, self.audit = AsyncMock(), AsyncMock(), AsyncMock()
        self.commands = ProductCommands(SimpleNamespace(outlets=self.collection, pos_outlet_jobs=SimpleNamespace(), pos_import_leases=None),
                                        self.push, self.delete, self.audit, resource="outlets", fields=("name", "status", "phone"))
        self.event = {"outlet_id": "o", "command_id": "command"}

    async def test_unknown_outcome_keeps_original_outlet_and_recoverable_intent(self):
        self.push.side_effect = HTTPException(502, {"status_code": 500, "url": "private-url"})
        with patch("pos_product_commands.import_lease", lease):
            with self.assertRaises(HTTPException):
                await self.commands.process("outlet-command", "b", self.event)
        self.assertEqual(self.outlet["name"], "Original")
        self.assertEqual(self.outlet["status"], "active")
        self.assertTrue(self.outlet["pos_sync_pending"])
        self.assertNotIn("private-url", str(self.outlet["pos_sync_error"]))

    async def test_archive_requires_pos_acknowledgement_and_retains_identity(self):
        self.outlet["pos_command"].update(operation="delete", changes={})
        with patch("pos_product_commands.import_lease", lease):
            await self.commands.process("outlet-command", "b", self.event)
        self.delete.assert_awaited_once()
        self.assertEqual(self.outlet["pos_external_id"], "source")
        self.assertEqual(self.outlet["status"], "inactive")
        self.assertTrue(self.outlet["deleted_at"])
        self.assertFalse(self.outlet["pos_sync_pending"])
        self.audit.assert_awaited_once()

    async def test_unmapped_or_unversioned_legacy_outlet_requires_review(self):
        for fields, code in [({"pos_external_id": None}, "POS_OUTLET_MAPPING_REQUIRED"), ({"pos_external_id": "source", "pos_metadata": {}}, "POS_OUTLET_REFRESH_REQUIRED")]:
            with self.assertRaises(HTTPException) as caught:
                await self.commands.enqueue({**self.outlet, **fields}, "update", {}, {"id": "a", "email": "a@example.test"})
            self.assertEqual(caught.exception.detail["code"], code)

    async def test_rejected_creation_can_be_corrected_without_guessing_an_existing_source(self):
        self.outlet.update(pos_external_id=None, pos_sync_pending=False)
        self.outlet['pos_command'].update(operation='create')
        self.collection.find_one_and_update = AsyncMock(return_value={**self.outlet, 'pos_sync_pending': True})
        self.commands.ensure_job = AsyncMock()
        await self.commands.enqueue(self.outlet, 'update', {'name': 'Corrected'}, {'id': 'a', 'email': 'a@example.test'})
        changes = self.collection.find_one_and_update.call_args.args[1]['$set']['pos_command']
        self.assertEqual(changes['operation'], 'create')
        self.assertEqual(changes['changes']['name'], 'Corrected')

    async def test_only_definitively_rejected_unlinked_creates_can_be_discarded(self):
        self.outlet.update(pos_external_id=None, pos_sync_pending=False)
        self.outlet['pos_command'].update(operation='create')
        self.commands.jobs.find_one = AsyncMock(return_value={'status': 'rejected'})
        self.collection.update_one = AsyncMock(return_value=SimpleNamespace(matched_count=1))
        self.assertTrue(await self.commands.discard_rejected_create(self.outlet))
        self.assertEqual(self.commands.jobs.find_one.call_args.args[0]['event.command_id'], 'command')
        self.assertEqual(self.collection.update_one.call_args.args[1]['$set']['status'], 'inactive')
        self.outlet['pos_sync_pending'] = True
        self.assertFalse(await self.commands.discard_rejected_create(self.outlet))
