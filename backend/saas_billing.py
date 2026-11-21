"""Charging restaurants for Taskoora with Razorpay Subscriptions (recurring UPI Autopay / card e-mandate).

Taskoora's own Razorpay account is configured with RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET.

Flow: an owner (from the POS) or a platform admin starts a checkout for a plan -> Razorpay returns a hosted page where
the owner sets up the mandate -> Razorpay charges every period and tells us through signed webhooks. Webhooks are the
only thing that changes a paid subscription: `activated`/`charged` make it active and move current_period_end forward;
`pending` (a charge is being retried) makes it past_due (still fully usable); `halted` (all retries failed), `paused`
and `completed` make it expired (read-only in the POS, data kept); `cancelled` makes it cancelled. The POS is told
first, and the subscription is only changed once the POS accepted it, exactly like the manual lifecycle; if the POS
cannot be reached the webhook fails and Razorpay retries it. Each webhook event and each payment is applied once.
"""
import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

import requests

# AdminCore plan slug -> POS plan key (limits and features enforced by the POS).
PLAN_TO_POS = {"free": "starter", "starter": "starter", "pro": "growth", "business": "enterprise"}

STATUS_BY_EVENT = {
    "subscription.activated": "active",
    "subscription.charged": "active",
    "subscription.resumed": "active",
    "subscription.pending": "past_due",
    "subscription.halted": "expired",
    "subscription.paused": "expired",
    "subscription.completed": "expired",
    "subscription.cancelled": "cancelled",
}
CYCLES = {"monthly": ("monthly", 120), "yearly": ("yearly", 10)}
CHECKOUT_REUSE_HOURS = 23


class BillingError(Exception):
    def __init__(self, status: int, message: str, code: str = "BILLING_ERROR", retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.retryable = retryable


def billing_keys(environ=os.environ):
    return {
        "key_id": (environ.get("RAZORPAY_KEY_ID") or "").strip(),
        "key_secret": (environ.get("RAZORPAY_KEY_SECRET") or "").strip(),
        "webhook_secret": (environ.get("RAZORPAY_WEBHOOK_SECRET") or "").strip(),
    }


def billing_configured(environ=os.environ) -> bool:
    keys = billing_keys(environ)
    return bool(keys["key_id"] and keys["key_secret"] and keys["webhook_secret"])


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not raw_body or not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("utf-8"), str(signature).encode("utf-8"))


def paise(rupees) -> int:
    return int(round(float(rupees) * 100))


def iso_from_unix(value):
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()


class RazorpayClient:
    BASE_URL = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, key_secret: str, session=None, timeout: float = 15.0):
        self.auth = "Basic " + base64.b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("ascii")
        self.session = session or requests.Session()
        self.timeout = timeout

    def request(self, method: str, path: str, payload=None) -> dict:
        try:
            response = self.session.request(method, f"{self.BASE_URL}{path}", json=payload, timeout=self.timeout,
                                            headers={"Authorization": self.auth, "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise BillingError(502, "Could not reach Razorpay", "RAZORPAY_UNREACHABLE", retryable=True) from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            description = str((data.get("error") or {}).get("description") or f"error {response.status_code}")[:200]
            status = 502 if response.status_code >= 500 or response.status_code == 401 else 400
            raise BillingError(status, f"Razorpay: {description}", "RAZORPAY_ERROR", retryable=response.status_code >= 500 or response.status_code == 429)
        return data

    def create_plan(self, payload):
        return self.request("POST", "/plans", payload)

    def create_subscription(self, payload):
        return self.request("POST", "/subscriptions", payload)

    def cancel_subscription(self, subscription_id: str, at_cycle_end: bool):
        return self.request("POST", f"/subscriptions/{subscription_id}/cancel", {"cancel_at_cycle_end": 1 if at_cycle_end else 0})


class SaasBilling:
    """`run` executes blocking Razorpay calls (server passes starlette's run_in_threadpool). `push_state(business_id,
    status, period_end, plan_slug)` tells the POS and raises if it did not accept the change."""

    def __init__(self, db, push_state, record_event, audit, logger, run, client=None, environ=os.environ, now=None):
        self.db = db
        self.push_state = push_state
        self.record_event = record_event
        self.audit = audit
        self.logger = logger
        self.run = run
        self.environ = environ
        self._client = client
        self._now = now

    def now(self):
        return self._now() if self._now else datetime.now(timezone.utc)

    def client(self) -> RazorpayClient:
        if self._client:
            return self._client
        if not billing_configured(self.environ):
            raise BillingError(503, "Online subscription payments are not set up yet (RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET)", "BILLING_NOT_CONFIGURED")
        keys = billing_keys(self.environ)
        self._client = RazorpayClient(keys["key_id"], keys["key_secret"])
        return self._client

    # ------------------------------------------------------------------ plans
    async def razorpay_plan_id(self, plan: dict, cycle: str) -> str:
        period, _ = CYCLES[cycle]
        amount = paise((plan.get("pricing") or {}).get(cycle) or 0)
        if amount <= 0:
            raise BillingError(400, "This plan has no price for that billing cycle", "PLAN_NOT_PAID")
        cache_key = f"{cycle}_{amount}"
        cached = (plan.get("razorpay_plans") or {}).get(cache_key)
        if cached:
            return cached
        created = await self.run(self.client().create_plan, {
            "period": period, "interval": 1,
            "item": {"name": f"{plan.get('name') or plan.get('slug')} ({cycle})"[:200], "amount": amount, "currency": "INR"},
            "notes": {"admincore_plan_id": plan["id"], "plan_slug": plan.get("slug") or ""},
        })
        # A price change gets a new Razorpay plan (new key); concurrent checkouts may create a spare one, which is harmless.
        await self.db.plans.update_one({"id": plan["id"]}, {"$set": {f"razorpay_plans.{cache_key}": created["id"]}})
        return created["id"]

    async def subscription_for(self, business_id: str) -> dict:
        sub = await self.db.subscriptions.find_one({"business_id": business_id}, {"_id": 0})
        if sub:
            return sub
        now = self.now().isoformat()
        sub = {"id": uuid.uuid4().hex, "business_id": business_id, "plan_id": None, "plan_slug": None, "status": "trial",
               "billing_cycle": "monthly", "current_period_start": now, "current_period_end": None, "trial_start": now, "trial_end": None,
               "cancelled_at": None, "billing_provider": None, "billing_provider_id": None, "metadata": {}, "created_at": now, "updated_at": now}
        await self.db.subscriptions.insert_one(dict(sub))
        return sub

    # ------------------------------------------------------------------ checkout
    async def start_checkout(self, business_id: str, plan_slug: str, cycle: str, actor: dict, notify_email=None, notify_phone=None) -> dict:
        if cycle not in CYCLES:
            raise BillingError(400, "billing_cycle must be monthly or yearly", "CYCLE_INVALID")
        plan = await self.db.plans.find_one({"slug": str(plan_slug or "").strip().lower(), "is_active": True}, {"_id": 0})
        if not plan:
            raise BillingError(404, "Plan not found", "PLAN_NOT_FOUND")
        sub = await self.subscription_for(business_id)
        if sub.get("billing_provider_id") and sub.get("status") in ("active", "past_due") and sub.get("plan_id") == plan["id"] and sub.get("billing_cycle") == cycle:
            raise BillingError(409, "This plan is already being paid for automatically", "ALREADY_SUBSCRIBED")
        pending = sub.get("pending_checkout") or {}
        if pending.get("razorpay_subscription_id"):
            fresh = pending.get("created_at", "") > (self.now() - timedelta(hours=CHECKOUT_REUSE_HOURS)).isoformat()
            if fresh and pending.get("plan_id") == plan["id"] and pending.get("billing_cycle") == cycle:
                return {"short_url": pending["short_url"], "razorpay_subscription_id": pending["razorpay_subscription_id"], "reused": True}
            try:
                await self.run(self.client().cancel_subscription, pending["razorpay_subscription_id"], False)
            except BillingError as exc:
                self.logger.warning("Old checkout %s not cancelled: %s", pending["razorpay_subscription_id"], exc.message)
        rz_plan = await self.razorpay_plan_id(plan, cycle)
        _, total_count = CYCLES[cycle]
        payload = {
            "plan_id": rz_plan, "total_count": total_count, "quantity": 1, "customer_notify": 1,
            "notes": {"admincore_subscription_id": sub["id"], "admincore_business_id": business_id, "plan_slug": plan["slug"], "billing_cycle": cycle},
        }
        notify = {k: v for k, v in {"notify_email": notify_email, "notify_phone": notify_phone}.items() if v}
        if notify:
            payload["notify_info"] = notify
        created = await self.run(self.client().create_subscription, payload)
        checkout = {"razorpay_subscription_id": created["id"], "short_url": created.get("short_url"), "plan_id": plan["id"],
                    "plan_slug": plan["slug"], "billing_cycle": cycle, "created_at": self.now().isoformat()}
        await self.db.subscriptions.update_one({"id": sub["id"]}, {"$set": {"pending_checkout": checkout, "updated_at": self.now().isoformat()}})
        await self.record_event(business_id, "CHECKOUT_STARTED", actor, {"plan_slug": plan["slug"], "billing_cycle": cycle, "razorpay_subscription_id": created["id"]})
        await self.audit(business_id, actor.get("id"), actor.get("email"), "checkout_started", "subscription", sub["id"], {"plan": plan["slug"], "cycle": cycle})
        return {"short_url": checkout["short_url"], "razorpay_subscription_id": created["id"], "reused": False}

    async def cancel(self, business_id: str, actor: dict) -> dict:
        sub = await self.db.subscriptions.find_one({"business_id": business_id}, {"_id": 0})
        if not sub or not sub.get("billing_provider_id") or sub.get("status") == "cancelled":
            raise BillingError(409, "There is no automatic payment to cancel", "NOTHING_TO_CANCEL")
        await self.run(self.client().cancel_subscription, sub["billing_provider_id"], True)
        await self.db.subscriptions.update_one({"id": sub["id"]}, {"$set": {"cancel_at_period_end": True, "updated_at": self.now().isoformat()}})
        await self.record_event(business_id, "CANCELLATION_REQUESTED", actor, {"effective": sub.get("current_period_end")})
        await self.audit(business_id, actor.get("id"), actor.get("email"), "cancellation_requested", "subscription", sub["id"], {})
        return {"cancel_at_period_end": True, "current_period_end": sub.get("current_period_end")}

    # ------------------------------------------------------------------ webhooks
    async def handle_webhook(self, raw_body: bytes, signature: str, event_id: str, body: dict) -> int:
        """200 applied or ignored, 401 bad signature, 503 not configured, 500 retry later (POS unreachable)."""
        secret = billing_keys(self.environ)["webhook_secret"]
        if not secret:
            return 503
        if not verify_signature(raw_body, signature, secret):
            return 401
        event_id = str(event_id or "").strip()
        if event_id and await self.db.billing_events.find_one({"event_id": event_id}):
            return 200
        event = str((body or {}).get("event") or "")
        payload = (body or {}).get("payload") or {}
        entity = (payload.get("subscription") or {}).get("entity") or {}
        notes = entity.get("notes") or {}
        sub = await self.db.subscriptions.find_one({"id": notes.get("admincore_subscription_id")}, {"_id": 0}) if notes.get("admincore_subscription_id") else None
        if sub and sub.get("business_id") == notes.get("admincore_business_id") and event.startswith("subscription."):
            try:
                await self.apply(sub, event, entity, payload.get("payment", {}).get("entity"), (body or {}).get("created_at"))
            except BillingError as exc:
                self.logger.warning("Billing webhook %s not applied yet: %s", event, exc.message)
                return 500
            except Exception as exc:  # the POS did not accept the change: let Razorpay retry
                self.logger.warning("Billing webhook %s not applied yet: %s", event, exc)
                return 500
        if event_id:
            try:
                await self.db.billing_events.insert_one({"event_id": event_id, "event": event[:60], "business_id": (sub or {}).get("business_id"),
                                                         "created_at": self.now().isoformat()})
            except Exception:  # already recorded by a parallel delivery
                pass
        return 200

    async def apply(self, sub: dict, event: str, entity: dict, payment: dict | None, event_created_at):
        rz_id = entity.get("id")
        pending = sub.get("pending_checkout") or {}
        is_pending = bool(rz_id) and pending.get("razorpay_subscription_id") == rz_id
        is_current = bool(rz_id) and sub.get("billing_provider_id") == rz_id
        if not (is_pending or is_current):
            return  # an old or replaced Razorpay subscription: nothing to change
        system = {"id": "razorpay", "email": "billing@razorpay"}
        now = self.now().isoformat()
        if payment and payment.get("id") and event == "subscription.charged":
            await self.record_payment(sub, entity, payment)
        new_status = STATUS_BY_EVENT.get(event)
        if not new_status:
            return
        stamp = int(event_created_at or 0)
        if stamp and stamp < int(sub.get("billing_last_event_at") or 0):
            return  # an older event arriving late never undoes a newer one
        if new_status == "active":
            plan_id = pending.get("plan_id") if is_pending else sub.get("plan_id")
            plan_slug = pending.get("plan_slug") if is_pending else sub.get("plan_slug")
            cycle = pending.get("billing_cycle") if is_pending else sub.get("billing_cycle")
            period_end = iso_from_unix(entity.get("current_end")) or sub.get("current_period_end")
            period_start = iso_from_unix(entity.get("current_start")) or sub.get("current_period_start")
            await self.push_state(sub["business_id"], "active", period_end, plan_slug)
            update = {"status": "active", "plan_id": plan_id, "plan_slug": plan_slug, "billing_cycle": cycle, "billing_provider": "razorpay",
                      "billing_provider_id": rz_id, "current_period_start": period_start, "current_period_end": period_end,
                      "trial_end": None, "billing_last_event_at": stamp or sub.get("billing_last_event_at"), "updated_at": now}
            if is_pending:
                update["pending_checkout"] = None
            await self.db.subscriptions.update_one({"id": sub["id"]}, {"$set": update})
            if plan_slug:
                await self.db.businesses.update_one({"id": sub["business_id"]}, {"$set": {"plan": plan_slug}})
            old = sub.get("billing_provider_id")
            if is_pending and old and old != rz_id:
                # The new plan is paid for: the old mandate must stop charging.
                try:
                    await self.run(self.client().cancel_subscription, old, False)
                except BillingError as exc:
                    self.logger.warning("Replaced Razorpay subscription %s not cancelled: %s", old, exc.message)
            if sub.get("status") != "active" or is_pending:
                await self.record_event(sub["business_id"], "SUBSCRIPTION_ACTIVE", system, {"razorpay_subscription_id": rz_id, "period_end": period_end})
            return
        if is_pending:
            if new_status == "cancelled":
                await self.db.subscriptions.update_one({"id": sub["id"]}, {"$set": {"pending_checkout": None, "updated_at": now}})
            return  # a checkout that never became active does not change the current subscription
        await self.push_state(sub["business_id"], new_status, sub.get("current_period_end"), sub.get("plan_slug"))
        update = {"status": new_status, "billing_last_event_at": stamp or sub.get("billing_last_event_at"), "updated_at": now}
        if new_status == "cancelled":
            update["cancelled_at"] = now
        await self.db.subscriptions.update_one({"id": sub["id"]}, {"$set": update})
        await self.record_event(sub["business_id"], f"SUBSCRIPTION_{new_status.upper()}", system, {"razorpay_event": event, "razorpay_subscription_id": rz_id})
        await self.audit(sub["business_id"], "system", "billing@razorpay", "status_changed", "subscription", sub["id"],
                         {"from_status": sub.get("status"), "to_status": new_status, "reason": event})

    async def record_payment(self, sub: dict, entity: dict, payment: dict):
        if await self.db.billing_payments.find_one({"payment_id": payment["id"]}):
            return
        doc = {
            "id": uuid.uuid4().hex, "payment_id": payment["id"], "business_id": sub["business_id"], "subscription_id": sub["id"],
            "razorpay_subscription_id": entity.get("id"), "invoice_id": payment.get("invoice_id"), "amount": (payment.get("amount") or 0) / 100,
            "currency": payment.get("currency") or "INR", "status": payment.get("status"), "method": payment.get("method"),
            "period_start": iso_from_unix(entity.get("current_start")), "period_end": iso_from_unix(entity.get("current_end")),
            "created_at": self.now().isoformat(),
        }
        try:
            await self.db.billing_payments.insert_one(doc)
        except Exception:  # unique payment_id: a parallel delivery recorded it
            pass

    # ------------------------------------------------------------------ status for owners
    async def status_for(self, business_id: str) -> dict:
        sub = await self.db.subscriptions.find_one({"business_id": business_id}, {"_id": 0}) or {}
        plans = await self.db.plans.find({"is_active": True}, {"_id": 0, "id": 1, "slug": 1, "name": 1, "description": 1, "pricing": 1, "limits": 1, "sort_order": 1}).to_list(50)
        payments = await self.db.billing_payments.find({"business_id": business_id}, {"_id": 0}).sort("created_at", -1).to_list(24)
        pending = sub.get("pending_checkout") or None
        return {
            "configured": billing_configured(self.environ) or bool(self._client),
            "status": sub.get("status"),
            "plan_slug": sub.get("plan_slug"),
            "billing_cycle": sub.get("billing_cycle"),
            "current_period_end": sub.get("current_period_end"),
            "trial_end": sub.get("trial_end"),
            "auto_pay": bool(sub.get("billing_provider_id")) and sub.get("status") not in ("cancelled", "expired"),
            "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
            "pending_checkout": {"short_url": pending.get("short_url"), "plan_slug": pending.get("plan_slug"), "billing_cycle": pending.get("billing_cycle")} if pending else None,
            "plans": sorted([{k: v for k, v in plan.items() if k != "razorpay_plans"} for plan in plans], key=lambda row: row.get("sort_order") or 0),
            "payments": [{k: row.get(k) for k in ("payment_id", "amount", "currency", "status", "method", "invoice_id", "period_start", "period_end", "created_at")} for row in payments],
        }
