"""Automatic subscription lifecycle (expiry) for AdminCore.

Modes (SUBSCRIPTION_AUTO_EXPIRY):
  off     - nothing changes automatically (default; statuses are managed by hand)
  trials  - a trial past its trial_end becomes "expired"
  all     - trials as above, plus a paid period past current_period_end becomes "past_due" (still fully usable),
            and "expired" once SUBSCRIPTION_GRACE_DAYS have also passed

Turn on "all" only once renewals are recorded (by hand or by a billing provider) by moving current_period_end forward;
otherwise every paying customer becomes read-only at the end of their first period. Data is never deleted: an
expired tenant keeps read-only access in the POS.
"""
import os
from datetime import datetime, timedelta, timezone

VALID_MODES = {"off", "trials", "all"}


def expiry_mode(environ=os.environ) -> str:
    mode = (environ.get("SUBSCRIPTION_AUTO_EXPIRY") or "off").strip().lower()
    return mode if mode in VALID_MODES else "off"


def grace_days(environ=os.environ) -> int:
    try:
        return max(0, int(environ.get("SUBSCRIPTION_GRACE_DAYS", "7")))
    except ValueError:
        return 7


def expiry_rules(mode: str, now: datetime, grace: int):
    """(query, new_status) pairs. Timestamps are stored as UTC ISO strings, which sort chronologically."""
    now_iso = now.isoformat()
    rules = []
    if mode in {"trials", "all"}:
        rules.append(({"status": {"$in": ["trial", "trialing"]}, "trial_end": {"$ne": None, "$lt": now_iso}}, "expired"))
    if mode == "all":
        rules.append(({"status": {"$in": ["active", "renewal"]}, "current_period_end": {"$ne": None, "$lt": now_iso}}, "past_due"))
        cutoff = (now - timedelta(days=grace)).isoformat()
        rules.append(({"status": "past_due", "current_period_end": {"$ne": None, "$lt": cutoff}}, "expired"))
    return rules


async def run_expiry(db, push_to_pos, record_event, audit, logger, now=None, environ=os.environ, batch=200) -> dict:
    """Apply due transitions. The POS is told first; a subscription is only changed here once the POS accepted it,
    so both systems never disagree. A conditional update makes concurrent runs (several API instances) safe."""
    mode = expiry_mode(environ)
    result = {"mode": mode, "changed": 0, "failed": 0}
    if mode == "off":
        return result
    now = now or datetime.now(timezone.utc)
    for query, new_status in expiry_rules(mode, now, grace_days(environ)):
        rows = await db.subscriptions.find(query, {"_id": 0}).to_list(batch)
        for sub in rows:
            try:
                await push_to_pos(sub["business_id"], new_status, sub.get("current_period_end"))
            except Exception as exc:  # the POS is unreachable: leave it for the next run
                result["failed"] += 1
                logger.warning("Subscription %s not moved to %s; POS did not accept it: %s", sub.get("id"), new_status, exc)
                continue
            applied = await db.subscriptions.update_one(
                {"id": sub["id"], "status": sub["status"]},
                {"$set": {"status": new_status, "updated_at": now.isoformat()}},
            )
            if not applied.modified_count:
                continue
            result["changed"] += 1
            details = {"from_status": sub["status"], "to_status": new_status, "reason": "automatic lifecycle",
                       "trial_end": sub.get("trial_end"), "current_period_end": sub.get("current_period_end")}
            await record_event(sub["business_id"], f"SUBSCRIPTION_{new_status.upper()}", {"id": "system"}, details)
            await audit(sub["business_id"], "system", "system@taskoora", "status_changed", "subscription", sub["id"], details)
    return result
