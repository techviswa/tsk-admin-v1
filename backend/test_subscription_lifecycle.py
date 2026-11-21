import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import subscription_lifecycle as lifecycle

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
iso = lambda delta_days: (NOW + timedelta(days=delta_days)).isoformat()


class FakeSubscriptions:
    def __init__(self, rows):
        self.rows = rows

    def _match(self, row, query):
        for key, cond in query.items():
            value = row.get(key)
            if isinstance(cond, dict):
                if "$in" in cond and value not in cond["$in"]:
                    return False
                if "$ne" in cond and value == cond["$ne"]:
                    return False
                if "$lt" in cond and not (value is not None and value < cond["$lt"]):
                    return False
            elif value != cond:
                return False
        return True

    def find(self, query, projection=None):
        matched = [dict(row) for row in self.rows if self._match(row, query)]
        return SimpleNamespace(to_list=AsyncMock(return_value=matched))

    async def update_one(self, query, update):
        for row in self.rows:
            if self._match(row, query):
                row.update(update["$set"])
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def rows():
    return [
        {"id": "t-old", "business_id": "b1", "status": "trial", "trial_end": iso(-1), "current_period_end": iso(20)},
        {"id": "t-new", "business_id": "b2", "status": "trial", "trial_end": iso(3), "current_period_end": iso(20)},
        {"id": "p-lapsed", "business_id": "b3", "status": "active", "trial_end": None, "current_period_end": iso(-2)},
        {"id": "p-grace-over", "business_id": "b4", "status": "past_due", "trial_end": None, "current_period_end": iso(-10)},
        {"id": "p-ok", "business_id": "b5", "status": "active", "trial_end": None, "current_period_end": iso(10)},
        {"id": "cancelled", "business_id": "b6", "status": "cancelled", "trial_end": iso(-30), "current_period_end": iso(-30)},
    ]


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def run_mode(self, mode, data, push=None):
        db = SimpleNamespace(subscriptions=FakeSubscriptions(data))
        push = push or AsyncMock()
        events, audit = AsyncMock(), AsyncMock()
        outcome = await lifecycle.run_expiry(db, push, events, audit, logging.getLogger("t"), now=NOW,
                                             environ={"SUBSCRIPTION_AUTO_EXPIRY": mode, "SUBSCRIPTION_GRACE_DAYS": "7"})
        return outcome, {row["id"]: row["status"] for row in data}, push, audit

    async def test_off_by_default_changes_nothing(self):
        outcome, states, push, _ = await self.run_mode("", rows())
        self.assertEqual(outcome["changed"], 0)
        push.assert_not_awaited()
        self.assertEqual(states["t-old"], "trial")

    async def test_trials_mode_only_expires_ended_trials(self):
        outcome, states, _, audit = await self.run_mode("trials", rows())
        self.assertEqual(states, {"t-old": "expired", "t-new": "trial", "p-lapsed": "active", "p-grace-over": "past_due", "p-ok": "active", "cancelled": "cancelled"})
        self.assertEqual(outcome["changed"], 1)
        self.assertEqual(audit.await_args.args[2], "system@taskoora")

    async def test_all_mode_gives_a_grace_period_before_expiry(self):
        _, states, _, _ = await self.run_mode("all", rows())
        self.assertEqual(states["p-lapsed"], "past_due")
        self.assertEqual(states["p-grace-over"], "expired")
        self.assertEqual(states["p-ok"], "active")
        self.assertEqual(states["cancelled"], "cancelled")

    async def test_nothing_is_recorded_when_the_pos_rejects(self):
        outcome, states, _, audit = await self.run_mode("trials", rows(), push=AsyncMock(side_effect=RuntimeError("pos down")))
        self.assertEqual(states["t-old"], "trial")
        self.assertEqual(outcome["failed"], 1)
        audit.assert_not_awaited()

    async def test_second_run_is_a_no_op(self):
        data = rows()
        await self.run_mode("all", data)
        outcome, _, _, _ = await self.run_mode("all", data)
        self.assertEqual(outcome["changed"], 0, "a subscription still inside its grace period stays past_due")


if __name__ == "__main__":
    unittest.main()
