"""An administrator's plan change must reach the POS (which enforces the plan) before AdminCore records it."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "admincore_plan_push_tests"}):
    import server

REQUEST = SimpleNamespace(cookies={}, headers={})
ADMIN = {"id": "root", "email": "root@x.com", "role": "platform_admin", "business_ids": []}
PLANS = {
    "p-starter": {"id": "p-starter", "slug": "starter", "name": "Starter", "trial_days": 14},
    "p-business": {"id": "p-business", "slug": "business", "name": "Business"},
    "p-custom": {"id": "p-custom", "slug": "custom-deal", "name": "Custom"},
}


def fake_db(linked=True, existing_sub=True):
    business = {"id": "b1", "name": "Cafe", "status": "active"}
    if linked:
        business.update(pos_external_id="pos-b1", pos_tenant_id="pos-t1")
    sub = {"id": "s1", "business_id": "b1", "plan_id": "p-starter", "plan_slug": "starter", "status": "trial",
           "current_period_end": "2026-12-31T00:00:00+00:00"}
    subs_find = AsyncMock(side_effect=[sub, sub] if existing_sub else [None])

    async def plan_lookup(query, *_args, **_kwargs):
        return PLANS.get(query.get("id"))

    return SimpleNamespace(
        businesses=SimpleNamespace(find_one=AsyncMock(return_value=business), update_one=AsyncMock()),
        plans=SimpleNamespace(find_one=AsyncMock(side_effect=plan_lookup)),
        subscriptions=SimpleNamespace(find_one=subs_find, update_one=AsyncMock(), insert_one=AsyncMock()),
    )


class AdminPlanPushTests(unittest.IsolatedAsyncioTestCase):
    def patches(self, db, pos_request):
        return [
            patch.object(server, "db", db),
            patch.object(server, "POS_CORE_API_BASE_URL", "https://pos.example.com"),
            patch.object(server, "get_current_user", new_callable=AsyncMock, return_value=ADMIN),
            patch.object(server, "validate_business_access", new_callable=AsyncMock),
            patch.object(server, "pos_headers_for_admin_business", new_callable=AsyncMock, return_value={}),
            patch.object(server, "pos_core_session_request", pos_request),
            patch.object(server, "record_subscription_event", new_callable=AsyncMock),
            patch.object(server, "create_audit_log", new_callable=AsyncMock),
        ]

    async def run_with(self, db, pos_request, coro_factory):
        managers = self.patches(db, pos_request)
        for manager in managers:
            manager.start()
        try:
            return await coro_factory()
        finally:
            for manager in reversed(managers):
                manager.stop()

    async def test_plan_change_sends_the_pos_plan_before_saving(self):
        db, pos = fake_db(), AsyncMock(return_value={"ok": True})
        await self.run_with(db, pos, lambda: server.update_subscription(
            "s1", server.SubscriptionUpdate(plan_id="p-business", status="active"), REQUEST))
        method, path = pos.await_args.args
        self.assertEqual((method, path), ("PUT", "admincore/tenants/pos-b1/subscription"))
        payload = pos.await_args.kwargs["json"]
        self.assertEqual(payload["plan"], "enterprise")
        self.assertEqual(payload["subscription_status"], "active")
        saved = db.subscriptions.update_one.await_args.args[1]["$set"]
        self.assertEqual((saved["plan_id"], saved["plan_slug"]), ("p-business", "business"))

    async def test_plan_change_alone_keeps_the_current_status(self):
        db, pos = fake_db(), AsyncMock(return_value={"ok": True})
        await self.run_with(db, pos, lambda: server.update_subscription(
            "s1", server.SubscriptionUpdate(plan_id="p-business"), REQUEST))
        payload = pos.await_args.kwargs["json"]
        self.assertEqual((payload["plan"], payload["subscription_status"]), ("enterprise", "trialing"))

    async def test_nothing_is_saved_when_the_pos_rejects_the_plan(self):
        db = fake_db()
        pos = AsyncMock(side_effect=server.HTTPException(status_code=502, detail="pos down"))
        with self.assertRaises(server.HTTPException) as raised:
            await self.run_with(db, pos, lambda: server.update_subscription(
                "s1", server.SubscriptionUpdate(plan_id="p-business"), REQUEST))
        self.assertEqual(raised.exception.status_code, 502)
        db.subscriptions.update_one.assert_not_awaited()
        db.businesses.update_one.assert_not_awaited()

    async def test_plan_without_pos_equivalent_is_refused_for_linked_business(self):
        db, pos = fake_db(), AsyncMock()
        with self.assertRaises(server.HTTPException) as raised:
            await self.run_with(db, pos, lambda: server.update_subscription(
                "s1", server.SubscriptionUpdate(plan_id="p-custom"), REQUEST))
        self.assertEqual(raised.exception.status_code, 400)
        pos.assert_not_awaited()
        db.subscriptions.update_one.assert_not_awaited()

    async def test_unlinked_business_changes_plan_without_calling_the_pos(self):
        db, pos = fake_db(linked=False), AsyncMock()
        await self.run_with(db, pos, lambda: server.update_subscription(
            "s1", server.SubscriptionUpdate(plan_id="p-custom"), REQUEST))
        pos.assert_not_awaited()
        db.subscriptions.update_one.assert_awaited_once()

    async def test_status_only_change_still_uses_the_status_push(self):
        db, pos = fake_db(), AsyncMock(return_value={"ok": True})
        await self.run_with(db, pos, lambda: server.update_subscription(
            "s1", server.SubscriptionUpdate(status="active"), REQUEST))
        payload = pos.await_args.kwargs["json"]
        self.assertNotIn("plan", payload)
        self.assertEqual(payload["subscription_status"], "active")

    async def test_new_subscription_sends_its_plan_to_the_pos(self):
        db, pos = fake_db(existing_sub=False), AsyncMock(return_value={"ok": True})
        await self.run_with(db, pos, lambda: server.create_subscription(
            server.SubscriptionCreate(business_id="b1", plan_id="p-business", status="active"), REQUEST))
        payload = pos.await_args.kwargs["json"]
        self.assertEqual((payload["plan"], payload["subscription_status"]), ("enterprise", "active"))
        db.subscriptions.insert_one.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
