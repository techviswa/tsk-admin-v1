"""Durable profile changes with a checkpoint for each POS business."""
from uuid import uuid4

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from pos_passwords import hash_pos_password
from pos_sync_worker import PosSyncWorker, RejectedSyncUpdate, timestamp


class PosProfileUpdates:
    def __init__(self, db, push, audit, ready=None, authorize=None):
        self.db = db
        self.jobs = db.pos_profile_jobs
        self.push = push
        self.audit = audit
        self.authorize = authorize
        self.worker = PosSyncWorker(self.jobs, self.process, ready=ready)

    async def enqueue(self, existing, changes, password, actor, operation="update", original_sync_business_ids=None):
        user_id = existing["id"]
        event = {"operation_id": str(uuid4()), "operation": operation, "user_id": user_id,
                 "changes": changes, "baseline": {key: existing.get(key) for key in changes if key != "updated_at"},
                 "previous_email": existing.get("email"), "password": hash_pos_password(password),
                 "business_ids": changes.get("business_ids", existing.get("business_ids", [])),
                 "original_business_ids": existing.get("business_ids", []),
                 "original_sync_business_ids": (existing.get("business_ids", [])
                                                if original_sync_business_ids is None else original_sync_business_ids),
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
            job = await self.jobs.find_one({"_id": user_id}, {"status": 1, "run_after": 1, "last_error": 1}) or {}
            raise HTTPException(status_code=409, detail={
                "code": "POS_PROFILE_UPDATE_UNFINISHED",
                "message": "The previous update has not completed. Wait for automatic retries, or retry a failed update before submitting new credentials.",
                "status": job.get("status", "pending"),
                "retry_at": job.get("run_after"),
            }) from exc
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
        targets = list(dict.fromkeys([*event.get("original_sync_business_ids", event["original_business_ids"]), *event["business_ids"]]))
        for target in targets:
            if target in completed:
                continue
            outgoing = desired if target in event["business_ids"] else {
                **current, "status": "inactive", "business_ids": event["original_business_ids"]}
            if self.authorize:
                outgoing = {**outgoing, '_pos_actor_headers': await self.authorize(event, target, 'staff')}
            try:
                result = await self.push(outgoing, (event.get("password") or None) if target in event["business_ids"] else None,
                                         target_business_id=target, previous_email=event.get("previous_email"),
                                         changed_fields=tuple(changes) if target in event['business_ids'] else ('status',),
                                         command_id=event['operation_id'])
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                response = detail.get("response") or {}
                upstream = response.get("error") if isinstance(response, dict) else None
                # These POS codes are emitted before its user write. Unknown 500s,
                # timeouts and any partially applied multi-business job stay guarded.
                if not completed and detail.get("status_code") in [400, 403, 409] and isinstance(upstream, dict) and upstream.get("code") in {
                    "EMAIL_EXISTS", "EMAIL_REQUIRED", "INVALID_ROLE", "INVALID_PERMISSION", "OUTLET_NOT_IN_BUSINESS", "LAST_OWNER", "STAFF_VERSION_CONFLICT", "ACTOR_PERMISSION_REVOKED", "USER_ADMIN_FORBIDDEN",
                }:
                    raise RejectedSyncUpdate({**detail, "message": upstream.get("message") or "POS rejected the user details"}) from exc
                raise
            if not result:
                raise HTTPException(status_code=503, detail="POS profile update is not configured")
            await self.jobs.update_one(
                {"_id": user_id, "event.operation_id": event["operation_id"]},
                {"$addToSet": {"event.completed_business_ids": target}},
            )
            completed.add(target)
        query = {"id": user_id}
        if event["baseline"]:
            query["$and"] = [{key: {"$in": [original, changes[key]]}} for key, original in event["baseline"].items()]
        update = {"$set": changes}
        if current.get('pos_creation_pending'):
            update['$set'] = {**changes, 'pos_creation_pending': False}
        if any(key in changes and changes[key] != current.get(key) for key in ("password_hash", "email", "role", "status", "business_ids")):
            update["$inc"] = {"token_version": 1}
        applied = await self.db.users.update_one(query, update)
        if not applied.matched_count:
            raise HTTPException(status_code=409, detail="Profile changed during POS synchronization; reconciliation is required")
        if "email" in changes:
            await self.db.businesses.update_many(
                {"id": {"$in": event["business_ids"]}, "pos_owner_email": event.get("previous_email")},
                {"$set": {"pos_owner_email": changes["email"]}},
            )
        await self.audit((event["business_ids"] or [None])[0], event["actor_id"], event["actor_email"],
                         "synced", "user", user_id, {"pos_update_operation": event["operation_id"],
                         "operation": event.get("operation", "update"),
                         "delivery_actor_id": event.get('delivery_actor_id', event['actor_id'])})
        return {"status": "success", "user_id": user_id}
