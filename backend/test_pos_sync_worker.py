import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pymongo.errors import DuplicateKeyError

from pos_retry import retry_after_seconds
from pos_sync_worker import PosSyncWorker


class SyncWorkerRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_snapshot_request_reuses_active_job(self):
        active = {"id": "pos-snapshot:a:products", "status": "running"}
        collection = SimpleNamespace(find_one_and_update=AsyncMock(side_effect=DuplicateKeyError("active job")),
                                     find_one=AsyncMock(return_value=active))
        result = await PosSyncWorker(collection, AsyncMock()).enqueue_snapshot("products", "a")
        self.assertEqual(result, active)
        query = collection.find_one_and_update.call_args.args[0]
        self.assertEqual(query["_id"], active["id"])
        self.assertIn("running", query["status"]["$nin"])

    def test_http_date_and_invalid_retry_headers(self):
        self.assertEqual(retry_after_seconds("Thu, 01 Jan 1970 00:05:00 GMT", 100), 200)
        for value in [None, "invalid", "NaN", "Infinity"]:
            self.assertEqual(retry_after_seconds(value, 100), 300)

    async def test_rate_limit_preserves_job_on_last_attempt(self):
        job = {"_id": "job", "resource": "products", "business_id": "a", "attempts": 5, "max_attempts": 5}
        collection = SimpleNamespace(find_one_and_update=AsyncMock(return_value=job), update_one=AsyncMock())
        detail = {"code": "POS_RATE_LIMITED", "retry_after_seconds": 600}
        processor = AsyncMock(return_value={"status": "failed", "error_count": 1, "errors": [{"detail": detail}]})
        before = datetime.now(timezone.utc)
        result = await PosSyncWorker(collection, processor).run_once()
        self.assertEqual(result["status"], "retrying")
        self.assertEqual(result["attempts"], 4)
        self.assertGreaterEqual((datetime.fromisoformat(result["run_after"]) - before).total_seconds(), 600)
        self.assertEqual(result["last_error"], [{"detail": detail}])

    async def test_permanent_error_still_exhausts_retry_budget(self):
        collection = SimpleNamespace(find_one_and_update=AsyncMock(return_value={
            "_id": "job", "resource": "products", "business_id": "a", "attempts": 5}), update_one=AsyncMock())
        result = await PosSyncWorker(collection, AsyncMock(side_effect=ValueError("invalid scope"))).run_once()
        self.assertEqual(result["status"], "failed")
