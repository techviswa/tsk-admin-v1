"""Browser-independent, resumable snapshot scheduling and cross-process import leases."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from fastapi import HTTPException


def stamp(seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


async def reconcile_import_failures(collection, business_id, resource, snapshot_started_at, scope):
    """A verified complete snapshot supersedes older read-model delivery failures.

    Keep the original error and attempts for investigation. This never applies to
    credential/product/outlet commands or work received after the snapshot began.
    """
    await collection.update_many({'business_id': business_id, 'resource': resource, 'status': 'failed',
                                  'created_at': {'$lte': snapshot_started_at}},
        {'$set': {'status': 'reconciled', 'resolved_at': stamp(),
                  'resolution': 'complete_source_snapshot', 'resolution_scope': scope,
                  'resolution_snapshot_started_at': snapshot_started_at}})


@asynccontextmanager
async def import_lease(collection, business_id, resource, seconds=120):
    key, owner = f"import:{business_id}:{resource}", uuid4().hex
    try:
        row = await collection.find_one_and_update(
            {"_id": key, "$or": [{"until": {"$lte": stamp()}}, {"until": {"$exists": False}}]},
            {"$set": {"owner": owner, "until": stamp(seconds)}}, upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        raise HTTPException(409, {"code": "POS_IMPORT_BUSY", "message": "A refresh for this business and module is already running. Retry shortly."}) from None
    if not row or row.get("owner") != owner:
        raise HTTPException(409, "POS import lease unavailable")
    parent = asyncio.current_task()
    async def renew():
        while True:
            await asyncio.sleep(seconds / 3)
            try:
                result = await collection.update_one({"_id": key, "owner": owner}, {"$set": {"until": stamp(seconds)}})
                if result.matched_count != 1:
                    parent.cancel("POS import lease lost")
                    return
            except Exception:
                parent.cancel("POS import lease could not be renewed")
                return
    heartbeat = asyncio.create_task(renew())
    try:
        yield
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await collection.update_one({"_id": key, "owner": owner}, {"$set": {"until": stamp()}})


class PosReconciler:
    def __init__(self, db, worker, resources, interval=600):
        self.db, self.worker, self.resources = db, worker, resources
        self.interval = max(60, interval)

    async def discover(self):
        query = {"pos_external_id": {"$type": "string", "$ne": ""}, "pos_tenant_id": {"$type": "string", "$ne": ""},
                 "status": {"$ne": "inactive"}, "pos_provisioning_status": {"$nin": ["pending", "failed"]}}
        async for business in self.db.businesses.find(query, {"_id": 0, "id": 1}):
            await self.db.pos_reconcile_schedule.update_one({"_id": business["id"]},
                {"$setOnInsert": {"next_run": stamp(), "lease_until": stamp()}}, upsert=True)

    async def run_once(self):
        owner = uuid4().hex
        schedule = await self.db.pos_reconcile_schedule.find_one_and_update(
            {"next_run": {"$lte": stamp()}, "lease_until": {"$lte": stamp()}},
            {"$set": {"lease": owner, "lease_until": stamp(120)}},
            sort=[("next_run", 1), ("_id", 1)], return_document=ReturnDocument.AFTER)
        if not schedule:
            return False
        business = await self.db.businesses.find_one({"id": schedule["_id"]}, {"_id": 0})
        if business and business.get("pos_external_id") and business.get("pos_tenant_id") and business.get("status") not in {"inactive", "deleted"}:
            for resource in self.resources:
                await self.worker.enqueue_snapshot(resource, business["id"], fresh_for_seconds=self.interval)
        await self.db.pos_reconcile_schedule.update_one({"_id": schedule["_id"], "lease": owner},
            {"$set": {"next_run": stamp(self.interval), "lease_until": stamp(), "last_scheduled_at": stamp()}, "$unset": {"lease": ""}})
        return True

    async def run_forever(self):
        import logging
        while True:
            try:
                await self.discover()
                for _ in range(20):
                    if not await self.run_once():
                        break
            except Exception:
                logging.exception("POS reconciliation scheduler failed; its durable schedule remains pending")
            await asyncio.sleep(30)
