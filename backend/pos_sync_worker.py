"""Durable receipt and retry processing for authenticated POS change notifications."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pos_retry import rate_limit_delay


class PosImportError(Exception):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(str(detail))


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class PosSyncWorker:
    def __init__(self, collection, processor):
        self.collection = collection
        self.processor = processor

    async def enqueue_snapshot(self, resource, business_id):
        job_id = f"pos-snapshot:{business_id}:{resource}"
        now = timestamp()
        try:
            return await self.collection.find_one_and_update(
                {"_id": job_id, "status": {"$nin": ["pending", "retrying", "running"]}},
                {"$set": {"id": job_id, "resource": resource, "business_id": business_id,
                          "event": {"action": "snapshot"}, "status": "pending", "attempts": 0,
                          "max_attempts": 5, "run_after": now, "created_at": now,
                          "updated_at": now, "last_error": None},
                 "$unset": {"result": "", "finished_at": "", "lease": "", "lease_until": ""}},
                upsert=True, return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # The stable ID makes concurrent clicks reuse an already active job.
            return await self.collection.find_one({"_id": job_id})

    async def enqueue(self, event_id, resource, business_id, event=None):
        job_id = f"pos-change:{event_id}"
        await self.collection.update_one(
            {"_id": job_id},
            {"$setOnInsert": {
                "id": job_id, "resource": resource, "business_id": business_id,
                "event": event,
                "status": "pending", "attempts": 0, "max_attempts": 5,
                "run_after": timestamp(), "created_at": timestamp(),
            }}, upsert=True,
        )
        job = await self.collection.find_one({"_id": job_id}, {"_id": 0})
        if job["business_id"] != business_id or job["resource"] != resource:
            raise ValueError("POS event ID was already used for another business or resource")
        if job.get("event") != event:
            raise ValueError("POS event ID was replayed with different event content")
        return job

    async def run_once(self):
        now = timestamp()
        lease = str(uuid4())
        job = await self.collection.find_one_and_update(
            {"$or": [
                {"status": {"$in": ["pending", "retrying"]}, "run_after": {"$lte": now}},
                {"status": "running", "lease_until": {"$lte": now}},
            ]},
            {"$set": {"status": "running", "lease": lease, "updated_at": now,
                       "lease_until": (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat()},
             "$inc": {"attempts": 1}},
            sort=[("run_after", 1)], return_document=ReturnDocument.AFTER,
        )
        if not job:
            return None
        try:
            if job["attempts"] > job.get("max_attempts", 5):
                raise RuntimeError("Sync retry limit reached after worker restart")
            args = (job["resource"], job["business_id"], job["event"]) if job.get("event") else (job["resource"], job["business_id"])
            result = await asyncio.wait_for(self.processor(*args), timeout=300)
            if result.get("error_count", 0) or result.get("status") != "success":
                raise PosImportError(result.get("errors") or "POS import failed")
            update = {"status": "synced", "result": result, "last_error": None, "finished_at": timestamp()}
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
            cooldown = rate_limit_delay(detail)
            retry = bool(cooldown) or job["attempts"] < job.get("max_attempts", 5)
            update = {"status": "retrying" if retry else "failed", "last_error": detail,
                      "run_after": (datetime.now(timezone.utc) + timedelta(seconds=max(cooldown, min(300, 5 * 2 ** min(job["attempts"], 6))))).isoformat()}
            if cooldown:
                update["attempts"] = job["attempts"] - 1
        update["updated_at"] = timestamp()
        await self.collection.update_one({"_id": job["_id"], "lease": lease},
                                         {"$set": update, "$unset": {"lease": "", "lease_until": ""}})
        return update

    async def run_forever(self):
        while True:
            try:
                await self.run_once()
            except Exception:
                logging.exception("POS sync worker failed; pending jobs remain in MongoDB")
            await asyncio.sleep(2)
