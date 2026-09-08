"""Bounded, sequential bridge synchronization with explicit skipped results."""

import asyncio
import time


def fatal_sync_result(result):
    for error in result.get("errors", []):
        detail = error.get("detail") or {}
        if not isinstance(detail, dict):
            continue
        status = detail.get("status_code")
        if str(status) in {"401", "403", "429"} or (str(status).isdigit() and int(status) >= 500):
            return True
        if detail.get("code") in {
            "POS_RATE_LIMITED", "POS_BRIDGE_RESOURCE_TIMEOUT", "POS_SYNC_BATCH_TIMEOUT",
            "POS_TENANT_SCOPE_MISSING", "POS_TENANT_SCOPE_MISMATCH",
        }:
            return True
    return False


async def run_sync_batch(resources, sync_one, *, timeout_seconds=20):
    deadline = time.monotonic() + timeout_seconds
    results = {}
    stop_reason = None
    for resource in resources:
        remaining = deadline - time.monotonic()
        if remaining <= 0 and not stop_reason:
            stop_reason = "POS_SYNC_BATCH_TIMEOUT"
        if stop_reason:
            results[resource] = {
                "resource": resource, "status": "skipped", "count": 0, "error_count": 0,
                "reason": stop_reason,
            }
            continue
        try:
            _, result = await asyncio.wait_for(sync_one(resource), timeout=remaining)
        except asyncio.TimeoutError:
            result = {
                "resource": resource, "status": "failed", "count": 0, "error_count": 1,
                "errors": [{"reason": "Sync time limit reached; retry the unfinished resources.",
                            "detail": {"code": "POS_SYNC_BATCH_TIMEOUT", "status_code": 504}}],
            }
        results[resource] = result
        if fatal_sync_result(result):
            stop_reason = "POS_SYNC_STOPPED_AFTER_FAILURE"
    statuses = [result.get("status") for result in results.values()]
    status = "success" if all(item == "success" for item in statuses) else (
        "partial" if any(item in {"success", "partial"} for item in statuses) else "failed"
    )
    return {"results": results, "status": status,
            "skipped_count": statuses.count("skipped"), "stopped_reason": stop_reason}
