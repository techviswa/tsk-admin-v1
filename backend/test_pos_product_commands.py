import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from pos_product_commands import ProductCommands
from pos_sync_worker import RejectedSyncUpdate


@asynccontextmanager
async def lease(*args, **kwargs):
    yield


class ProductCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.product = {"id": "p", "business_id": "b", "name": "Old", "price": 10, "pos_sync_pending": True,
            "pos_command": {"id": "c", "version": 1, "operation": "update", "changes": {"name": "New"}, "actor_id": "a", "actor_email": "a@example.com"}}
        async def update(query, changes, **kwargs):
            for key, value in changes.get("$set", {}).items():
                if key.startswith("pos_command."):
                    self.product["pos_command"][key.split('.', 1)[1]] = value
                else:
                    self.product[key] = value
            return SimpleNamespace(matched_count=1)
        self.collection = SimpleNamespace(find_one=AsyncMock(side_effect=lambda *args, **kwargs: deepcopy(self.product)), update_one=AsyncMock(side_effect=update))
        self.db = SimpleNamespace(products=self.collection, pos_product_jobs=SimpleNamespace(), pos_import_leases=None)
        self.push = AsyncMock(return_value={"data": {"id": "source", "name": "New", "price": 12}})
        self.audit = AsyncMock()
        self.commands = ProductCommands(self.db, self.push, AsyncMock(), self.audit)
        self.event = {"product_id": "p", "command_id": "c"}

    async def test_unconfirmed_update_never_changes_confirmed_fields(self):
        self.push.side_effect = HTTPException(502, {"status_code": 500, "url": "private-url"})
        with patch('pos_product_commands.import_lease', lease):
            with self.assertRaises(HTTPException):
                await self.commands.process('product-command', 'b', self.event)
        self.assertEqual(self.product['name'], 'Old')
        self.assertEqual(self.product['price'], 10)
        self.assertTrue(self.product['pos_sync_pending'])
        self.assertNotIn('private-url', str(self.product['pos_sync_error']))

    async def test_audit_recovery_does_not_repeat_a_committed_pos_update(self):
        self.audit.side_effect = [RuntimeError('audit unavailable'), None]
        with patch('pos_product_commands.import_lease', lease):
            with self.assertRaisesRegex(RuntimeError, 'audit unavailable'):
                await self.commands.process('product-command', 'b', self.event)
            await self.commands.process('product-command', 'b', self.event)
        self.push.assert_awaited_once()
        self.assertEqual(self.product['name'], 'New')
        self.assertFalse(self.product['pos_sync_pending'])
        self.assertEqual(self.audit.call_args.kwargs['operation_id'], 'c')

    async def test_version_rejection_preserves_source_data_and_allows_review(self):
        self.push.side_effect = HTTPException(502, {"status_code": 409})
        with patch('pos_product_commands.import_lease', lease):
            with self.assertRaises(RejectedSyncUpdate):
                await self.commands.process('product-command', 'b', self.event)
        self.assertEqual(self.product['name'], 'Old')
        self.assertFalse(self.product['pos_sync_pending'])

    async def test_superseded_command_is_not_replayed(self):
        with patch('pos_product_commands.import_lease', lease):
            with self.assertRaises(RejectedSyncUpdate):
                await self.commands.process('product-command', 'b', {**self.event, 'command_id': 'old-command'})
        self.push.assert_not_awaited()
