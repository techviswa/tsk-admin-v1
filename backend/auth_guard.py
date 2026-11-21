"""Login throttling helpers for the AdminCore control plane.

Failed attempts are tracked per (email, client ip) and per email in MongoDB so the
limit survives restarts and is shared between API instances.
"""
from datetime import datetime, timedelta, timezone

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW = timedelta(minutes=15)


def attempt_keys(email: str, ip: str) -> list:
    email = (email or "").strip().lower()
    return [f"acct:{email}", f"pair:{email}|{ip or 'unknown'}"]


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def lockout_seconds_remaining(collection, email: str, ip: str, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    worst = 0
    for key in attempt_keys(email, ip):
        doc = await collection.find_one({"_id": key})
        locked_until = _aware((doc or {}).get("locked_until"))
        if locked_until:
            worst = max(worst, int((locked_until - now).total_seconds()))
    return max(0, worst)


async def record_failed_attempt(collection, email: str, ip: str, now=None) -> None:
    now = now or datetime.now(timezone.utc)
    for key in attempt_keys(email, ip):
        doc = await collection.find_one({"_id": key}) or {}
        window_start = _aware(doc.get("window_start"))
        if not window_start or now - window_start > LOCKOUT_WINDOW:
            doc = {}
            window_start = now
        count = doc.get("count", 0) + 1
        update = {"count": count, "window_start": window_start, "updated_at": now}
        if count >= MAX_FAILED_ATTEMPTS:
            update["locked_until"] = now + LOCKOUT_WINDOW
        await collection.update_one({"_id": key}, {"$set": update}, upsert=True)


async def clear_failed_attempts(collection, email: str, ip: str) -> None:
    await collection.delete_many({"_id": {"$in": attempt_keys(email, ip)}})
