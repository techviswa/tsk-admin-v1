"""Durable receipt and retry processing for authenticated POS change notifications."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pymongo import ReturnDocument


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class PosSyncWorker:
    def __init__(self, collection, processor):
        self.collection = collection
        self.processor = processor

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
            if result.get("error_count", 0) or result.get("status") in ["partial", "failed"]:
                raise RuntimeError(str(result.get("errors") or "POS import failed"))
            update = {"status": "synced", "result": result, "last_error": None, "finished_at": timestamp()}
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
            retry = job["attempts"] < job.get("max_attempts", 5)
            update = {"status": "retrying" if retry else "failed", "last_error": detail,
                      "run_after": (datetime.now(timezone.utc) + timedelta(seconds=min(300, 5 * 2 ** min(job["attempts"], 6)))).isoformat()}
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
