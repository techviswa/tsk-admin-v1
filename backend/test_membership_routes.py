"""Control-plane authority cannot leak from another business membership."""
import unittest
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException, Response
with patch.dict(os.environ, {'MONGO_URL': 'mongodb://127.0.0.1:27017', 'DB_NAME': 'membership_route_unit'}):
    import server


class MembershipRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_profile_retry_checks_every_intended_business_before_adopting_delivery(self):
        actor = {'id': 'replacement', 'email': 'replacement@example.test', 'role': 'platform_admin'}
        event = {'operation_id': 'same-operation', 'actor_id': 'original', 'business_ids': ['owned', 'other'],
                 'original_sync_business_ids': ['owned'], 'changes': {}}
        jobs = SimpleNamespace(find_one=AsyncMock(return_value={'status': 'failed', 'event': event}), update_one=AsyncMock())
        database = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value={'id': 'target', 'business_ids': ['owned']})),
                                   pos_profile_jobs=jobs)
        for allow_all in [False, True]:
            jobs.update_one.reset_mock()
            async def authority(user, business_id, **kwargs):
                if business_id == 'other' and not allow_all:
                    raise HTTPException(403, 'No authority in the remaining target')
            audit = AsyncMock()
            with patch.multiple(server, db=database, get_current_user=AsyncMock(return_value=actor),
                recover_business_assignments=AsyncMock(return_value=['owned']),
                require_business_management=AsyncMock(side_effect=authority), require_business_module_enabled=AsyncMock(),
                create_audit_log=audit):
                if not allow_all:
                    with self.assertRaises(HTTPException):
                        await server.sync_user_to_pos('target', None)
                    jobs.update_one.assert_not_awaited()
                    audit.assert_not_awaited()
                else:
                    await server.sync_user_to_pos('target', None)
                    changes = jobs.update_one.call_args.args[1]['$set']
                    self.assertEqual(changes['event.delivery_actor_id'], 'replacement')
                    self.assertNotIn('event.actor_id', changes)
                    self.assertEqual(jobs.update_one.call_args.args[0]['event.operation_id'], 'same-operation')
                    audit.assert_awaited_once()

    async def test_queued_commands_recheck_current_actor_and_selected_membership(self):
        actor = {'id': 'actor', 'status': 'active', 'role': 'manager', 'business_ids': ['business'],
                 'pos_links': {'business': {'user_id': 'source-actor', 'role': 'manager', 'status': 'active', 'permissions': ['products']}}}
        database = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=actor)))
        with patch.object(server, 'db', database), patch.object(server, 'require_business_management', AsyncMock()):
            headers = await server.queued_pos_actor_headers({'actor_id': 'actor'}, 'business', 'products')
            self.assertEqual(headers['x-admincore-actor-id'], 'source-actor')
            actor['pos_links']['business']['permissions'] = []
            self.assertEqual(await server.queued_pos_actor_headers({'actor_id': 'actor'}, 'business', 'products'), {'x-admincore-actor-mode': 'denied'})
            actor.update(role='platform_security')
            self.assertEqual(await server.queued_pos_actor_headers({'actor_id': 'actor'}, 'business', 'products'), {'x-admincore-actor-mode': 'denied'})
            self.assertEqual((await server.queued_pos_actor_headers({'actor_id': 'actor'}, 'business', 'staff'))['x-admincore-actor-mode'], 'platform')
            actor['status'] = 'inactive'
            self.assertEqual(await server.queued_pos_actor_headers({'actor_id': 'actor'}, 'business', 'staff'), {'x-admincore-actor-mode': 'denied'})

    def setUp(self):
        self.actor = {'id': 'actor', 'email': 'actor@example.test', 'role': 'business_owner',
                      'business_ids': ['owned', 'staffed'], 'pos_links': {
                          'owned': {'role': 'business_owner', 'status': 'active'},
                          'staffed': {'role': 'staff', 'status': 'active', 'permissions': ['settings']}}}
        self.business = {'id': 'staffed', 'status': 'active', 'pos_external_id': 'source'}
        record = SimpleNamespace(find_one=AsyncMock(return_value={'id': 'item', 'business_id': 'staffed'}),
                                 update_one=AsyncMock(), delete_one=AsyncMock(), insert_one=AsyncMock())
        self.database = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value=self.business)),
                                        feature_flags=record, integrations=record, settings=record)

    async def test_staff_membership_cannot_administer_or_read_secrets_despite_global_owner(self):
        calls = [
            lambda: server.update_business('staffed', server.BusinessUpdate(name='Wrong'), None),
            lambda: server.list_settings('staffed', None),
            lambda: server.update_setting('staffed', 'currency', server.SettingUpdate(value='USD'), None),
            lambda: server.list_feature_flags('staffed', None),
            lambda: server.create_feature_flag('staffed', server.FeatureFlagCreate(key='x', name='X'), None),
            lambda: server.update_feature_flag('item', server.FeatureFlagUpdate(enabled=True), None),
            lambda: server.delete_feature_flag('item', None),
            lambda: server.list_integrations('staffed', None),
            lambda: server.create_integration('staffed', server.IntegrationCreate(slug='x', name='X', type='api'), None),
            lambda: server.update_integration('item', server.IntegrationUpdate(name='Wrong'), None),
            lambda: server.delete_integration('item', None),
            lambda: server.list_audit_logs('staffed', None),
            lambda: server.get_control_center_overview(None, 'staffed'),
        ]
        with patch.object(server, 'get_current_user', AsyncMock(return_value=self.actor)), patch.object(server, 'db', self.database):
            for call in calls:
                with self.subTest(route=call):
                    with self.assertRaises(HTTPException) as caught:
                        await call()
                    self.assertEqual(caught.exception.status_code, 403)
        self.database.settings.update_one.assert_not_awaited()
        self.database.integrations.delete_one.assert_not_awaited()

    async def test_real_selected_owner_can_administer_and_inactive_membership_wins(self):
        with patch.object(server, 'db', self.database):
            await server.require_business_admin_scope(self.actor, 'owned', permission='settings.write', owner_only=True)
            self.actor['pos_links']['owned']['status'] = 'inactive'
            with self.assertRaises(HTTPException):
                await server.require_business_admin_scope(self.actor, 'owned', permission='settings.write')

    async def test_explicit_settings_revocation_applies_to_selected_manager(self):
        self.actor['pos_links']['staffed'] = {'role': 'manager', 'permissions': [], 'status': 'active'}
        with patch.object(server, 'db', self.database):
            with self.assertRaises(HTTPException):
                await server.require_business_admin_scope(self.actor, 'staffed', permission='settings.write')
            self.actor['pos_links']['staffed']['permissions'] = ['settings']
            await server.require_business_admin_scope(self.actor, 'staffed', permission='settings.write')
            with self.assertRaises(HTTPException):
                await server.require_business_admin_scope(self.actor, 'staffed', permission='integrations.write', owner_only=True)

    async def test_platform_permissions_do_not_follow_tenant_management_defaults(self):
        with patch.object(server, 'db', self.database):
            for role in ['platform_auditor', 'platform_finance', 'platform_security', 'platform_operations']:
                with self.subTest(role=role), self.assertRaises(HTTPException):
                    await server.require_business_admin_scope({'role': role}, 'staffed', permission='integrations.write')
            await server.require_business_admin_scope({'role': 'platform_admin'}, 'staffed', permission='integrations.write')

    async def test_suspended_business_cannot_enter_an_aggregate_read_scope(self):
        self.business['status'] = 'suspended'
        with patch.object(server, 'db', self.database):
            with self.assertRaises(HTTPException) as caught:
                await server.pos_business_filter(self.actor, 'staffed')
            self.assertEqual(caught.exception.status_code, 403)
            combined = await server.pos_business_filter(self.actor, None)
            self.assertEqual(combined['business_id'], {'$in': []})

    async def test_pos_linked_user_deletion_queues_deactivation_instead_of_losing_the_mapping(self):
        target = {'id': 'target', 'role': 'staff', 'email': 'target@example.test', 'business_ids': ['owned']}
        users = SimpleNamespace(find_one=AsyncMock(return_value=target), delete_one=AsyncMock())
        self.database.users = users
        enqueue = AsyncMock()
        with patch.multiple(server, get_current_user=AsyncMock(return_value=self.actor), db=self.database,
                            POS_CORE_API_BASE_URL='http://pos.test',
                            require_business_module_enabled=AsyncMock(),
                            pos_profile_updates=SimpleNamespace(enqueue=enqueue)):
            response = await server.delete_user('target', None)
        self.assertEqual(response.status_code, 202)
        enqueue.assert_awaited_once_with(target, {'status': 'inactive'}, None, self.actor)
        users.delete_one.assert_not_awaited()

    async def test_tenant_cannot_create_unassigned_accounts(self):
        with patch.object(server, 'get_current_user', AsyncMock(return_value=self.actor)):
            with self.assertRaises(HTTPException) as caught:
                await server.create_user(server.UserCreate(email='orphan@example.test', password='long-enough', name='Orphan', role='staff', business_ids=[]), None, Response())
        self.assertEqual(caught.exception.status_code, 403)
