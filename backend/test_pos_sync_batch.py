import asyncio
import unittest

from pos_sync_batch import run_sync_batch


class SyncBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_resources_are_sequential(self):
        active = 0
        calls = []

        async def sync(resource):
            nonlocal active
            active += 1
            self.assertEqual(active, 1)
            await asyncio.sleep(0)
            calls.append(resource)
            active -= 1
            return resource, {"status": "success"}

        result = await run_sync_batch(["products", "bills", "reports"], sync)
        self.assertEqual(calls, ["products", "bills", "reports"])
        self.assertEqual(result["status"], "success")

    async def test_fatal_failures_stop_remaining_requests(self):
        for status in [401, 403, 429, 500, 502, 503]:
            calls = []

            async def sync(resource):
                calls.append(resource)
                return resource, {"status": "failed", "errors": [{"detail": {"status_code": status}}]}

            result = await run_sync_batch(["products", "bills"], sync)
            self.assertEqual(calls, ["products"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["results"]["bills"]["status"], "skipped")

    async def test_missing_endpoint_does_not_block_other_resources(self):
        async def sync(resource):
            return resource, ({"status": "failed", "errors": [{"detail": {"status_code": 404}}]}
                              if resource == "customers" else {"status": "success"})

        result = await run_sync_batch(["customers", "products"], sync)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["results"]["products"]["status"], "success")

    async def test_deadline_cancels_resource_and_marks_rest_skipped(self):
        cancelled = asyncio.Event()

        async def sync(resource):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        result = await run_sync_batch(["products", "bills"], sync, timeout_seconds=0.02)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["skipped_count"], 1)

    async def test_tenant_mismatch_stops_batch(self):
        async def sync(resource):
            return resource, {"status": "failed", "errors": [{"detail": {"code": "POS_TENANT_SCOPE_MISMATCH"}}]}

        result = await run_sync_batch(["products", "bills"], sync)
        self.assertEqual(result["skipped_count"], 1)

    async def test_external_cancellation_propagates(self):
        async def sync(resource):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await run_sync_batch(["products"], sync)


if __name__ == "__main__":
    unittest.main()
