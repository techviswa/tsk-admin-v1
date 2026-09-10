"""Durable profile changes with a checkpoint for each POS business."""
from uuid import uuid4

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from pos_passwords import hash_pos_password
from pos_sync_worker import PosSyncWorker, timestamp


class PosProfileUpdates:
    def __init__(self, db, push, audit):
        self.db = db
        self.jobs = db.pos_profile_jobs
        self.push = push
        self.audit = audit
        self.worker = PosSyncWorker(self.jobs, self.process)

    async def enqueue(self, existing, changes, password, actor, operation="update"):
        user_id = existing["id"]
        event = {"operation_id": str(uuid4()), "operation": operation, "user_id": user_id,
                 "changes": changes, "baseline": {key: existing.get(key) for key in changes if key != "updated_at"},
                 "previous_email": existing.get("email"), "password": hash_pos_password(password),
                 "business_ids": changes.get("business_ids", existing.get("business_ids", [])),
                 "original_business_ids": existing.get("business_ids", []),
                 "completed_business_ids": [], "actor_id": actor["id"], "actor_email": actor["email"]}
        now = timestamp()
        try:
            await self.jobs.find_one_and_update(
                {"_id": user_id, "status": {"$nin": ["pending", "running", "retrying", "failed"]}},
                {"$set": {"id": user_id, "resource": "profile", "business_id": user_id,
                          "event": event, "status": "pending", "attempts": 0, "max_attempts": 5,
                          "run_after": now, "created_at": now, "updated_at": now, "last_error": None},
                 "$unset": {"result": "", "finished_at": ""}},
                upsert=True, return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail="A POS profile update is pending or failed. Finish or retry it before editing again.") from exc
        return {"status": "pending", "user_id": user_id}

    async def process(self, resource, business_id, event):
        user_id = event["user_id"]
        current = await self.db.users.find_one({"id": user_id}, {"_id": 0})
        if not current:
            raise HTTPException(status_code=409, detail="User was removed while a POS profile update was pending")
        changes = event["changes"]
        if current.get("business_ids", []) not in [event["original_business_ids"], event["business_ids"]]:
            raise HTTPException(status_code=409, detail="Business assignments changed while the POS profile update was pending")
        for key, original in event["baseline"].items():
            if current.get(key) not in [original, changes[key]]:
                raise HTTPException(status_code=409, detail=f"User {key} changed while the POS update was pending; reconciliation is required")
        desired = {**current, **changes}
        completed = set(event.get("completed_business_ids", []))
        targets = list(dict.fromkeys([*event["original_business_ids"], *event["business_ids"]]))
        for target in targets:
            if target in completed:
                continue
            outgoing = desired if target in event["business_ids"] else {
                **current, "status": "inactive", "business_ids": event["original_business_ids"]}
            result = await self.push(outgoing, (event.get("password") or None) if target in event["business_ids"] else None,
                                     target_business_id=target, previous_email=event.get("previous_email"))
            if not result:
                raise HTTPException(status_code=503, detail="POS profile update is not configured")
            await self.jobs.update_one(
                {"_id": user_id, "event.operation_id": event["operation_id"]},
                {"$addToSet": {"event.completed_business_ids": target}},
            )
        query = {"id": user_id}
        if event["baseline"]:
            query["$and"] = [{key: {"$in": [original, changes[key]]}} for key, original in event["baseline"].items()]
        applied = await self.db.users.update_one(query, {"$set": changes})
        if not applied.matched_count:
            raise HTTPException(status_code=409, detail="Profile changed during POS synchronization; reconciliation is required")
        if "email" in changes:
            await self.db.businesses.update_many(
                {"id": {"$in": event["business_ids"]}, "pos_owner_email": event.get("previous_email")},
                {"$set": {"pos_owner_email": changes["email"]}},
            )
        await self.audit((event["business_ids"] or [None])[0], event["actor_id"], event["actor_email"],
                         "synced", "user", user_id, {"pos_update_operation": event["operation_id"],
                         "operation": event.get("operation", "update")})
        return {"status": "success", "user_id": user_id}
