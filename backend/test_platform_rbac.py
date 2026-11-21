import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import platform_rbac as rbac

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "admincore_rbac_tests"}):
    import server


def user(role, uid="u1", business_ids=None):
    return {"id": uid, "email": f"{uid}@x.com", "role": role, "business_ids": business_ids or []}


REQUEST = SimpleNamespace(cookies={}, headers={})


class PermissionMatrixTests(unittest.TestCase):
    def test_super_admin_has_everything(self):
        self.assertTrue(rbac.has_platform_permission(user("platform_admin"), "plans.delete"))

    def test_finance_can_change_plans_but_not_suspend_tenants(self):
        finance = user("platform_finance")
        self.assertTrue(rbac.has_platform_permission(finance, "subscriptions.write"))
        self.assertFalse(rbac.has_platform_permission(finance, "businesses.suspend"))
        self.assertFalse(rbac.has_platform_permission(finance, "plans.delete"))

    def test_operations_can_suspend_but_not_touch_money(self):
        ops = user("platform_operations")
        self.assertTrue(rbac.has_platform_permission(ops, "businesses.suspend"))
        self.assertFalse(rbac.has_platform_permission(ops, "subscriptions.write"))

    def test_auditor_is_strictly_read_only(self):
        auditor = user("platform_auditor")
        for permission in rbac.PLATFORM_ROLE_PERMISSIONS["platform_auditor"]:
            self.assertTrue(permission.endswith(".read"), permission)
        self.assertFalse(rbac.has_platform_permission(auditor, "businesses.write"))

    def test_tenant_roles_never_hold_platform_permissions(self):
        for role in rbac.TENANT_ROLES:
            self.assertFalse(rbac.is_platform_staff(user(role)))
            self.assertFalse(rbac.has_platform_permission(user(role), "audit.read"))
        self.assertFalse(rbac.has_platform_permission({}, "audit.read"))

    def test_nobody_can_grant_a_role_above_their_own(self):
        self.assertTrue(rbac.can_assign_role(user("platform_admin"), "platform_auditor"))
        self.assertTrue(rbac.can_assign_role(user("business_owner"), "manager"))
        self.assertFalse(rbac.can_assign_role(user("business_owner"), "platform_admin"))
        self.assertFalse(rbac.can_assign_role(user("manager"), "business_owner"))
        self.assertFalse(rbac.can_assign_role(user("manager"), "manager"))
        self.assertFalse(rbac.can_assign_role(user("staff"), "staff"))
        self.assertFalse(rbac.can_assign_role(user("platform_security"), "staff"))


class RouteEnforcementTests(unittest.IsolatedAsyncioTestCase):
    def as_user(self, actor):
        return patch.object(server, "get_current_user", new_callable=AsyncMock, return_value=actor)

    async def assert_forbidden(self, coro):
        with self.assertRaises(server.HTTPException) as raised:
            await coro
        self.assertEqual(raised.exception.status_code, 403, raised.exception.detail)

    def business_db(self, **extra):
        businesses = SimpleNamespace(
            find_one=AsyncMock(return_value={"id": "b1", "name": "B", "status": "active", "plan": "starter"}),
            update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)),
        )
        return SimpleNamespace(businesses=businesses, audit_logs=SimpleNamespace(insert_one=AsyncMock()),
                               subscriptions=SimpleNamespace(find_one=AsyncMock(return_value=None)), **extra)

    async def test_owner_cannot_change_own_plan(self):
        owner = user("business_owner", business_ids=["b1"])
        with self.as_user(owner), patch.object(server, "db", self.business_db()):
            await self.assert_forbidden(server.update_business("b1", server.BusinessUpdate(plan="enterprise"), REQUEST))

    async def test_owner_cannot_change_business_status(self):
        owner = user("business_owner", business_ids=["b1"])
        with self.as_user(owner), patch.object(server, "db", self.business_db()):
            await self.assert_forbidden(server.update_business("b1", server.BusinessUpdate(status="suspended", reason="x" * 10), REQUEST))
            await self.assert_forbidden(server.update_business("b1", server.BusinessUpdate(status="inactive"), REQUEST))

    async def test_owner_can_edit_name_and_echoed_plan_is_ignored(self):
        owner = user("business_owner", business_ids=["b1"])
        fake = self.business_db()
        with self.as_user(owner), patch.object(server, "db", fake):
            await server.update_business("b1", server.BusinessUpdate(name="New", plan="starter", status="active"), REQUEST)
        update = fake.businesses.update_one.await_args.args[1]["$set"]
        self.assertEqual(update["name"], "New")
        self.assertNotIn("plan", update)
        self.assertNotIn("status", update)

    async def test_suspension_needs_reason_and_is_pushed_before_saving(self):
        ops = user("platform_operations", "ops")
        fake = self.business_db()
        with self.as_user(ops), patch.object(server, "db", fake), \
             patch.object(server, "push_business_lifecycle_to_pos", new_callable=AsyncMock) as push:
            with self.assertRaises(server.HTTPException) as raised:
                await server.update_business("b1", server.BusinessUpdate(status="suspended"), REQUEST)
            self.assertEqual(raised.exception.status_code, 400)
            push.assert_not_awaited()
            await server.update_business("b1", server.BusinessUpdate(status="suspended", reason="Non-payment, invoice 42"), REQUEST)
        push.assert_awaited_once()
        audit = fake.audit_logs.insert_one.await_args.args[0]
        self.assertEqual(audit["action"], "status_changed")
        self.assertEqual(audit["details"]["before"], {"status": "active"})
        self.assertEqual(audit["details"]["after"], {"status": "suspended"})
        self.assertIn("Non-payment", audit["details"]["reason"])

    async def test_state_is_not_saved_when_pos_rejects_the_change(self):
        admin = user("platform_admin", "root")
        fake = self.business_db()
        boom = server.HTTPException(status_code=502, detail="pos down")
        with self.as_user(admin), patch.object(server, "db", fake), \
             patch.object(server, "push_business_lifecycle_to_pos", new_callable=AsyncMock, side_effect=boom):
            with self.assertRaises(server.HTTPException):
                await server.update_business("b1", server.BusinessUpdate(status="suspended", reason="fraud review"), REQUEST)
        fake.businesses.update_one.assert_not_awaited()

    async def test_new_businesses_cannot_be_started_on_a_paid_plan_without_subscription_rights(self):
        for actor in (user("business_owner", business_ids=["b1"]), user("platform_operations", "ops")):
            with self.as_user(actor):
                await self.assert_forbidden(server.create_business(server.BusinessCreate(name="X", type="cafe", plan="enterprise"), REQUEST))
        with self.as_user(user("platform_auditor")):
            await self.assert_forbidden(server.create_business(server.BusinessCreate(name="X", type="cafe"), REQUEST))

    async def test_auditor_cannot_edit_business(self):
        with self.as_user(user("platform_auditor")), patch.object(server, "db", self.business_db()):
            await self.assert_forbidden(server.update_business("b1", server.BusinessUpdate(name="x"), REQUEST))

    async def test_business_owner_cannot_create_or_edit_subscriptions(self):
        owner = user("business_owner", business_ids=["b1"])
        with self.as_user(owner):
            await self.assert_forbidden(server.create_subscription(
                server.SubscriptionCreate(business_id="b1", plan_id="p", status="active"), REQUEST))
            await self.assert_forbidden(server.update_subscription("s1", server.SubscriptionUpdate(status="active"), REQUEST))
            await self.assert_forbidden(server.cancel_subscription("s1", REQUEST))

    async def test_only_super_admin_can_delete_plans(self):
        with self.as_user(user("platform_finance")):
            await self.assert_forbidden(server.delete_plan("p1", REQUEST))

    async def test_overrides_require_permission_and_reason(self):
        data = server.EntitlementOverrideCreate(business_id="b1", feature_code="qr.dynamic", enabled=True, reason="")
        with self.as_user(user("business_owner", business_ids=["b1"])):
            await self.assert_forbidden(server.upsert_entitlement_override(data, REQUEST))
        fake = self.business_db(business_entitlement_overrides=SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock()))
        with self.as_user(user("platform_finance", "fin")), patch.object(server, "db", fake):
            with self.assertRaises(server.HTTPException) as raised:
                await server.upsert_entitlement_override(data, REQUEST)
        self.assertEqual(raised.exception.status_code, 400)
        fake.business_entitlement_overrides.update_one.assert_not_awaited()

    async def test_staff_cannot_delete_feature_flags_integrations_or_outlets(self):
        staff = user("staff", business_ids=["b1"])
        with self.as_user(staff):
            await self.assert_forbidden(server.delete_feature_flag("f1", REQUEST))
            await self.assert_forbidden(server.update_feature_flag("f1", server.FeatureFlagUpdate(enabled=False), REQUEST))
            await self.assert_forbidden(server.delete_integration("i1", REQUEST))
        fake = SimpleNamespace(outlets=SimpleNamespace(find_one=AsyncMock(return_value={"id": "o1", "business_id": "b1"})))
        with self.as_user(staff), patch.object(server, "db", fake):
            await self.assert_forbidden(server.delete_outlet("o1", REQUEST))

    async def test_staff_cannot_mutate_pos_admin_records(self):
        staff = user("staff", business_ids=["b1"])
        with self.as_user(staff):
            await self.assert_forbidden(server.delete_pos_admin_record("payments", "r1", REQUEST))
            await self.assert_forbidden(server.create_pos_admin_record("payments", server.POSAdminRecord(title="t", business_id="b1"), REQUEST))

    async def test_manager_cannot_mint_an_owner(self):
        manager = user("manager", business_ids=["b1"])
        data = server.UserCreate(email="new@x.com", password="longenough1", name="N", role="business_owner", business_ids=["b1"])
        fake = self.business_db(users=SimpleNamespace(find_one=AsyncMock(return_value=None), insert_one=AsyncMock()))
        with self.as_user(manager), patch.object(server, "db", fake):
            await self.assert_forbidden(server.create_user(data, REQUEST, server.Response()))
        fake.users.insert_one.assert_not_awaited()

    async def test_owner_cannot_mint_platform_admin(self):
        owner = user("business_owner", business_ids=["b1"])
        data = server.UserCreate(email="new@x.com", password="longenough1", name="N", role="platform_admin", business_ids=[])
        fake = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=None), insert_one=AsyncMock()))
        with self.as_user(owner), patch.object(server, "db", fake):
            await self.assert_forbidden(server.create_user(data, REQUEST, server.Response()))

    async def test_cannot_delete_self_or_last_platform_admin(self):
        admin = user("platform_admin", "root")
        users = SimpleNamespace(
            find_one=AsyncMock(return_value={"id": "other", "role": "platform_admin", "business_ids": [], "email": "o@x.com"}),
            count_documents=AsyncMock(return_value=0),
            delete_one=AsyncMock(),
        )
        with self.as_user(admin), patch.object(server, "db", SimpleNamespace(users=users)):
            with self.assertRaises(server.HTTPException) as own:
                await server.delete_user("root", REQUEST)
            with self.assertRaises(server.HTTPException) as last:
                await server.delete_user("other", REQUEST)
        self.assertEqual(own.exception.status_code, 400)
        self.assertEqual(last.exception.status_code, 409)
        users.delete_one.assert_not_awaited()

    async def test_audit_log_listing_needs_audit_permission(self):
        with self.as_user(user("business_owner", business_ids=["b1"])):
            await self.assert_forbidden(server.list_all_audit_logs(REQUEST, 50, 0))

    async def test_suspended_business_locks_out_its_members_but_not_platform_staff(self):
        fake = SimpleNamespace(businesses=SimpleNamespace(find_one=AsyncMock(return_value={"id": "b1", "status": "suspended"})))
        with patch.object(server, "db", fake):
            with self.assertRaises(server.HTTPException) as raised:
                await server.validate_business_access(user("manager", business_ids=["b1"]), "b1")
            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(raised.exception.detail["code"], "BUSINESS_SUSPENDED")
            self.assertIsNotNone(await server.validate_business_access(user("platform_operations"), "b1"))


class BusinessCountLimitTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, actor, allowances):
        with patch.object(server, "get_limit", new_callable=AsyncMock, side_effect=lambda bid, code: allowances[bid]):
            await server.enforce_business_count_limit(actor)

    async def test_first_business_is_always_allowed(self):
        await self.check(user("business_owner", business_ids=[]), {})

    async def test_starter_owner_cannot_open_a_second_business(self):
        with self.assertRaises(server.HTTPException) as raised:
            await self.check(user("business_owner", business_ids=["b1"]), {"b1": 1})
        self.assertEqual(raised.exception.detail["code"], "PLAN_LIMIT_REACHED")

    async def test_most_generous_plan_wins(self):
        await self.check(user("business_owner", business_ids=["b1", "b2"]), {"b1": 1, "b2": "unlimited"})
        await self.check(user("business_owner", business_ids=["b1"]), {"b1": 3})

    async def test_platform_staff_are_not_limited(self):
        await self.check(user("platform_operations", business_ids=["b1"]), {"b1": 1})


class PosLifecycleMappingTests(unittest.TestCase):
    def test_suspended_business_always_maps_to_suspended(self):
        self.assertEqual(server.effective_pos_status("suspended", "active"), "suspended")
        self.assertEqual(server.effective_pos_status("inactive", "trial"), "suspended")

    def test_reactivated_business_keeps_the_subscription_restriction(self):
        self.assertEqual(server.effective_pos_status("active", "expired"), "expired")
        self.assertEqual(server.effective_pos_status("active", "trial"), "trialing")
        self.assertEqual(server.effective_pos_status("active", None), "active")


class ProductionBootstrapTests(unittest.IsolatedAsyncioTestCase):
    def fake_db(self):
        make = lambda: SimpleNamespace(insert_one=AsyncMock(), find_one=AsyncMock(return_value=None), update_one=AsyncMock())
        return SimpleNamespace(users=make(), modules=make(), plans=make())

    async def test_production_refuses_default_admin_password_and_seeds_no_demo_data(self):
        fake = self.fake_db()
        with patch.dict(os.environ, {"ADMIN_PASSWORD": ""}), patch.object(server, "db", fake), \
             patch.object(server, "PRODUCTION_CONFIG_STATUS", {"production": True}):
            await server.seed_data()
        fake.users.insert_one.assert_not_awaited()

    async def test_production_bootstrap_creates_only_admin_and_reference_data(self):
        fake = self.fake_db()
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "a-Strong-passphrase-42", "ADMIN_EMAIL": "root@taskoora.test"}), \
             patch.object(server, "db", fake), patch.object(server, "PRODUCTION_CONFIG_STATUS", {"production": True}):
            await server.seed_data()
        self.assertEqual(fake.users.insert_one.await_count, 1)
        self.assertEqual(fake.users.insert_one.await_args.args[0]["role"], "platform_admin")
        self.assertGreater(fake.plans.insert_one.await_count, 0)

    async def test_existing_admin_password_is_not_reset_to_the_default(self):
        fake = self.fake_db()
        fake.users.find_one = AsyncMock(return_value={"email": "admin@admin.com", "password_hash": server.hash_password("rotated-Password-1")})
        with patch.dict(os.environ, {"ADMIN_PASSWORD": ""}), patch.object(server, "db", fake), \
             patch.object(server, "PRODUCTION_CONFIG_STATUS", {"production": True}):
            await server.seed_data()
        fake.users.update_one.assert_not_awaited()

    async def test_restart_preserves_changed_admin_password_when_bootstrap_secret_remains(self):
        fake = self.fake_db()
        fake.users.find_one = AsyncMock(return_value={"email": "admin@admin.com", "password_hash": server.hash_password("new-user-chosen-password")})
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "old-bootstrap-password"}), patch.object(server, "db", fake):
            await server.seed_data()
        fake.users.update_one.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
