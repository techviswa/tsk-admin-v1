import copy
import hashlib
import hmac
import json
import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import saas_billing

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
SECRET = "whsec-test-secret"
ENV = {"RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": "s", "RAZORPAY_WEBHOOK_SECRET": SECRET}


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction):
        self.rows.sort(key=lambda row: row.get(key) or "", reverse=direction == -1)
        return self

    async def to_list(self, limit):
        return self.rows[:limit]


class Collection:
    def __init__(self, unique=None):
        self.rows = []
        self.unique = unique

    @staticmethod
    def match(row, query):
        return all(row.get(key) == value for key, value in query.items())

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if self.match(row, query):
                return copy.deepcopy(row)
        return None

    def find(self, query, projection=None):
        return Cursor([copy.deepcopy(row) for row in self.rows if self.match(row, query)])

    async def insert_one(self, doc):
        if self.unique and any(row.get(self.unique) == doc.get(self.unique) for row in self.rows):
            raise ValueError("duplicate key")
        self.rows.append(copy.deepcopy(doc))

    async def update_one(self, query, update):
        for row in self.rows:
            if self.match(row, query):
                for key, value in update["$set"].items():
                    target = row
                    parts = key.split(".")
                    for part in parts[:-1]:
                        target = target.setdefault(part, {})
                    target[parts[-1]] = value
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class FakeRazorpay:
    def __init__(self):
        self.calls = []
        self.counter = 0

    def _id(self, prefix):
        self.counter += 1
        return f"{prefix}_{self.counter}"

    def create_plan(self, payload):
        self.calls.append(("plan", payload))
        return {"id": self._id("plan")}

    def create_subscription(self, payload):
        self.calls.append(("subscription", payload))
        sub_id = self._id("sub")
        return {"id": sub_id, "short_url": f"https://rzp.io/i/{sub_id}", "status": "created"}

    def cancel_subscription(self, subscription_id, at_cycle_end):
        self.calls.append(("cancel", subscription_id, at_cycle_end))
        return {"id": subscription_id}


async def run_sync(fn, *args):
    return fn(*args)


def signed(body: dict):
    raw = json.dumps(body).encode("utf-8")
    return raw, hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


class SaasBillingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SimpleNamespace(
            plans=Collection(), subscriptions=Collection(), businesses=Collection(),
            billing_events=Collection(unique="event_id"), billing_payments=Collection(unique="payment_id"))
        self.db.plans.rows = [
            {"id": "p-free", "slug": "free", "name": "Free", "is_active": True, "pricing": {"monthly": 0, "yearly": 0}, "sort_order": 0},
            {"id": "p-pro", "slug": "pro", "name": "Pro", "is_active": True, "pricing": {"monthly": 2499, "yearly": 24990}, "sort_order": 2},
            {"id": "p-business", "slug": "business", "name": "Business", "is_active": True, "pricing": {"monthly": 4999, "yearly": 49990}, "sort_order": 3},
        ]
        self.db.businesses.rows = [{"id": "b1", "status": "active", "plan": "free"}]
        self.db.subscriptions.rows = [{"id": "s1", "business_id": "b1", "status": "trial", "plan_id": "p-free", "plan_slug": "free",
                                       "billing_cycle": "monthly", "current_period_end": None}]
        self.razorpay = FakeRazorpay()
        self.push = AsyncMock()
        self.events = AsyncMock()
        self.audit = AsyncMock()
        self.billing = saas_billing.SaasBilling(self.db, self.push, self.events, self.audit, logging.getLogger("t"), run_sync,
                                                client=self.razorpay, environ=ENV, now=lambda: NOW)
        self.owner = {"id": "u1", "email": "owner@example.com"}
        self.event_no = 0

    async def webhook(self, event, rz_sub, *, created_at=None, payment=None, event_id=None, sub_id="s1", business_id="b1", **entity_fields):
        self.event_no += 1
        entity = {"id": rz_sub, "notes": {"admincore_subscription_id": sub_id, "admincore_business_id": business_id}, **entity_fields}
        body = {"event": event, "created_at": created_at or 1000 + self.event_no, "payload": {"subscription": {"entity": entity}}}
        if payment:
            body["payload"]["payment"] = {"entity": payment}
        raw, signature = signed(body)
        return await self.billing.handle_webhook(raw, signature, event_id or f"evt_{self.event_no}", body)

    def sub(self):
        return self.db.subscriptions.rows[0]

    async def test_signature_and_configuration(self):
        raw, signature = signed({"event": "subscription.activated"})
        self.assertTrue(saas_billing.verify_signature(raw, signature, SECRET))
        self.assertFalse(saas_billing.verify_signature(raw, signature, "other"))
        self.assertFalse(saas_billing.verify_signature(raw + b" ", signature, SECRET))
        self.assertEqual(await self.billing.handle_webhook(raw, "bad", "e1", {}), 401)
        unconfigured = saas_billing.SaasBilling(self.db, self.push, self.events, self.audit, logging.getLogger("t"), run_sync, environ={})
        self.assertEqual(await unconfigured.handle_webhook(raw, signature, "e1", {}), 503)
        with self.assertRaises(saas_billing.BillingError) as ctx:
            unconfigured.client()
        self.assertEqual(ctx.exception.code, "BILLING_NOT_CONFIGURED")

    async def test_checkout_creates_plan_once_and_reuses_open_checkout(self):
        first = await self.billing.start_checkout("b1", "pro", "monthly", self.owner, notify_email="owner@example.com")
        self.assertEqual(first["short_url"], "https://rzp.io/i/sub_2")
        plan_call = self.razorpay.calls[0]
        self.assertEqual(plan_call[1]["item"]["amount"], 249900, "rupees become paise")
        self.assertEqual(plan_call[1]["period"], "monthly")
        sub_call = self.razorpay.calls[1][1]
        self.assertEqual(sub_call["notes"]["admincore_subscription_id"], "s1")
        self.assertEqual(sub_call["total_count"], 120)
        self.assertEqual(self.sub()["pending_checkout"]["plan_slug"], "pro")
        self.assertEqual(self.sub()["status"], "trial", "nothing changes until Razorpay confirms")
        again = await self.billing.start_checkout("b1", "pro", "monthly", self.owner)
        self.assertTrue(again["reused"])
        self.assertEqual(len(self.razorpay.calls), 2, "an open checkout is reused")
        yearly = await self.billing.start_checkout("b1", "pro", "yearly", self.owner)
        self.assertFalse(yearly["reused"])
        self.assertIn(("cancel", "sub_2", False), self.razorpay.calls, "the abandoned checkout is cancelled")
        self.assertEqual(self.db.plans.rows[1]["razorpay_plans"], {"monthly_249900": "plan_1", "yearly_2499000": "plan_3"})
        with self.assertRaises(saas_billing.BillingError) as ctx:
            await self.billing.start_checkout("b1", "free", "monthly", self.owner)
        self.assertEqual(ctx.exception.code, "PLAN_NOT_PAID")
        with self.assertRaises(saas_billing.BillingError):
            await self.billing.start_checkout("b1", "pro", "weekly", self.owner)

    async def test_activation_charges_and_lockout(self):
        await self.billing.start_checkout("b1", "pro", "monthly", self.owner)
        period_end = int((NOW + timedelta(days=30)).timestamp())
        self.assertEqual(await self.webhook("subscription.authenticated", "sub_2"), 200)
        self.push.assert_not_awaited()
        charge = {"id": "pay_1", "amount": 249900, "currency": "INR", "status": "captured", "method": "upi", "invoice_id": "inv_1"}
        self.assertEqual(await self.webhook("subscription.activated", "sub_2", current_start=int(NOW.timestamp()), current_end=period_end), 200)
        self.push.assert_awaited_with("b1", "active", saas_billing.iso_from_unix(period_end), "pro")
        self.assertEqual(self.sub()["status"], "active")
        self.assertEqual(self.sub()["plan_slug"], "pro")
        self.assertEqual(self.sub()["billing_provider_id"], "sub_2")
        self.assertIsNone(self.sub()["pending_checkout"])
        self.assertEqual(self.db.businesses.rows[0]["plan"], "pro")

        next_end = int((NOW + timedelta(days=60)).timestamp())
        self.assertEqual(await self.webhook("subscription.charged", "sub_2", payment=charge, current_end=next_end, event_id="evt_charge"), 200)
        self.assertEqual(await self.webhook("subscription.charged", "sub_2", payment=charge, current_end=next_end, event_id="evt_charge"), 200)
        self.assertEqual(await self.webhook("subscription.charged", "sub_2", payment=charge, current_end=next_end), 200)
        self.assertEqual(len(self.db.billing_payments.rows), 1, "a payment is recorded once however often it is reported")
        self.assertEqual(self.db.billing_payments.rows[0]["amount"], 2499)
        self.assertEqual(self.sub()["current_period_end"], saas_billing.iso_from_unix(next_end), "a renewal moves the paid period forward")

        self.assertEqual(await self.webhook("subscription.pending", "sub_2"), 200)
        self.assertEqual(self.sub()["status"], "past_due", "a failing charge keeps the POS usable while Razorpay retries")
        late = await self.webhook("subscription.activated", "sub_2", created_at=5)
        self.assertEqual(late, 200)
        self.assertEqual(self.sub()["status"], "past_due", "an old event arriving late never undoes a newer one")
        self.assertEqual(await self.webhook("subscription.halted", "sub_2"), 200)
        self.assertEqual(self.sub()["status"], "expired", "unpaid: read-only in the POS, data kept")
        self.push.assert_awaited_with("b1", "expired", saas_billing.iso_from_unix(next_end), "pro")

    async def test_pos_unreachable_means_retry(self):
        await self.billing.start_checkout("b1", "pro", "monthly", self.owner)
        self.push.side_effect = RuntimeError("POS down")
        self.assertEqual(await self.webhook("subscription.activated", "sub_2", event_id="evt_retry", current_end=2000000000), 500)
        self.assertEqual(self.sub()["status"], "trial", "nothing is saved unless the POS accepted it")
        self.assertEqual(self.db.billing_events.rows, [], "the event is not marked done, so Razorpay's retry is applied")
        self.push.side_effect = None
        self.assertEqual(await self.webhook("subscription.activated", "sub_2", event_id="evt_retry", current_end=2000000000), 200)
        self.assertEqual(self.sub()["status"], "active")

    async def test_plan_change_cancels_old_mandate_and_foreign_events_are_ignored(self):
        await self.billing.start_checkout("b1", "pro", "monthly", self.owner)
        await self.webhook("subscription.activated", "sub_2", current_end=2000000000)
        await self.billing.start_checkout("b1", "business", "monthly", self.owner)
        new_id = self.sub()["pending_checkout"]["razorpay_subscription_id"]
        self.assertEqual(self.sub()["plan_slug"], "pro", "still on the old plan until the new one is paid")
        await self.webhook("subscription.activated", new_id, current_end=2100000000)
        self.assertEqual(self.sub()["plan_slug"], "business")
        self.assertIn(("cancel", "sub_2", False), self.razorpay.calls, "the old plan's mandate stops charging")
        await self.webhook("subscription.cancelled", "sub_2")
        self.assertEqual(self.sub()["status"], "active", "events of the replaced mandate change nothing")
        await self.webhook("subscription.halted", new_id, business_id="someone-else")
        self.assertEqual(self.sub()["status"], "active", "notes must match the business")
        await self.webhook("subscription.halted", "sub_unknown")
        self.assertEqual(self.sub()["status"], "active")

    async def test_cancel_at_period_end_and_owner_status(self):
        with self.assertRaises(saas_billing.BillingError):
            await self.billing.cancel("b1", self.owner)
        await self.billing.start_checkout("b1", "pro", "monthly", self.owner)
        await self.webhook("subscription.activated", "sub_2", current_end=2000000000)
        result = await self.billing.cancel("b1", self.owner)
        self.assertTrue(result["cancel_at_period_end"])
        self.assertIn(("cancel", "sub_2", True), self.razorpay.calls, "paid time is kept until the period ends")
        self.assertEqual(self.sub()["status"], "active")
        await self.webhook("subscription.cancelled", "sub_2")
        self.assertEqual(self.sub()["status"], "cancelled")
        status = await self.billing.status_for("b1")
        self.assertEqual(status["status"], "cancelled")
        self.assertTrue(all("razorpay_plans" not in plan for plan in status["plans"]))
        self.assertEqual([plan["slug"] for plan in status["plans"]], ["free", "pro", "business"])


if __name__ == "__main__":
    unittest.main()
