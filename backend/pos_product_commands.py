"""Shared durable product/outlet commands; confirmed fields change after POS acknowledgement."""
from uuid import uuid4
from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pos_sync_worker import PosSyncWorker, RejectedSyncUpdate, timestamp
from pos_reconciliation import import_lease


class ProductCommands:
    def __init__(self, db, push, delete, audit, ready=None, *, resource="products", fields=None, authorize=None):
        self.db, self.push, self.delete, self.audit = db, push, delete, audit
        self.authorize = authorize
        if resource not in {"products", "outlets"}:
            raise ValueError("Unsupported command resource")
        self.resource, self.entity = resource, resource[:-1]
        self.records = getattr(db, resource)
        self.jobs = getattr(db, f"pos_{self.entity}_jobs")
        self.fields = fields or ("name", "price", "stock", "category", "active")
        self.worker = PosSyncWorker(self.jobs, self.process, ready=ready)

    def build_command(self, product, operation, changes, actor):
        return {"id": uuid4().hex, "version": 1, "operation": operation, "changes": changes,
                   "actor_id": actor["id"], "actor_email": actor["email"],
                   "source_updated_at": (product.get("pos_metadata") or {}).get("updated_at"), "created_at": timestamp()}

    async def enqueue(self, product, operation, changes, actor):
        if self.resource == "outlets" and operation in {"update", "delete"} and not product.get("pos_external_id"):
            prior = product.get("pos_command") or {}
            if operation == "update" and prior.get("operation") == "create" and prior.get("version") == 1 and not prior.get("completed_at") and not product.get("pos_sync_pending"):
                operation = "create"
            else:
                raise HTTPException(409, {"code": "POS_OUTLET_MAPPING_REQUIRED", "message": "Import or verify this outlet's POS source ID before editing it. Names and codes cannot establish identity."})
        if operation in {"update", "delete"} and product.get("pos_external_id") and not (product.get("pos_metadata") or {}).get("updated_at"):
            raise HTTPException(409, {"code": f"POS_{self.entity.upper()}_REFRESH_REQUIRED", "message": f"Refresh this {self.entity} from POS before changing it so its source version can be checked."})
        command = self.build_command(product, operation, changes, actor)
        claimed = await self.records.find_one_and_update(
            {"id": product["id"], "pos_sync_pending": {"$ne": True}},
            {"$set": {"pos_command": command, "pos_sync_pending": True, "pos_sync_error": None}},
            return_document=ReturnDocument.AFTER)
        if not claimed:
            raise HTTPException(409, {"code": f"{self.entity.upper()}_UPDATE_PENDING", "message": f"This {self.entity} already has an unfinished POS update. Wait or retry that update first."})
        await self.ensure_job(claimed)
        return claimed

    async def ensure_job(self, product):
        command = product["pos_command"]
        current = await self.jobs.find_one({"_id": product["id"]})
        if current and (current.get("status") in {"pending", "running", "retrying", "failed"}):
            return
        now = timestamp()
        try:
            await self.jobs.update_one({"_id": product["id"], "status": {"$nin": ["pending", "running", "retrying", "failed"]}}, {"$set": {
            "id": product["id"], "resource": f"{self.entity}-command", "business_id": product["business_id"],
            "event": {f"{self.entity}_id": product["id"], "command_id": command["id"]},
            "status": "pending", "attempts": 0, "max_attempts": 5, "run_after": now,
            "created_at": now, "updated_at": now, "last_error": None},
                "$unset": {"lease": "", "lease_until": "", "finished_at": "", "result": ""}}, upsert=True)
        except DuplicateKeyError:
            return

    async def record_audit(self, business_id, product, command):
        await self.audit(business_id, command["actor_id"], command["actor_email"], command["operation"], self.entity, product["id"],
                         {"pos_confirmed": True, "command_id": command["id"]}, operation_id=command["id"])

    async def discard_rejected_create(self, record):
        command = record.get("pos_command") or {}
        if record.get("pos_external_id") or record.get("pos_sync_pending") or command.get("operation") != "create" or command.get("completed_at"):
            return False
        rejected = await self.jobs.find_one({"_id": record["id"], "status": "rejected", "event.command_id": command.get("id")})
        if not rejected:
            return False
        result = await self.records.update_one({"id": record["id"], "pos_command.id": command.get("id"), "pos_sync_pending": False},
            {"$set": {"deleted_at": timestamp(), "status": "inactive"}})
        return result.matched_count == 1

    async def process(self, resource, business_id, event):
        async with import_lease(self.db.pos_import_leases, business_id, self.resource):
            product = await self.records.find_one({"id": event[f"{self.entity}_id"], "business_id": business_id}, {"_id": 0})
            command = (product or {}).get("pos_command") or {}
            if command.get("id") != event["command_id"]:
                raise RejectedSyncUpdate(f"The {self.entity} intent was superseded; it was not replayed")
            if not product.get("pos_sync_pending"):
                if command.get("completed_at"):
                    await self.record_audit(business_id, product, command)
                return {"status": "success", "error_count": 0}
            candidate = {**product, **command["changes"]}
            candidate["pos_command"] = command
            if self.authorize:
                candidate['_pos_actor_headers'] = await self.authorize(command, business_id, self.resource)
            try:
                if command["operation"] == "delete":
                    try:
                        await self.delete(candidate)
                    except HTTPException as exc:
                        if not isinstance(exc.detail, dict) or exc.detail.get("status_code") != 404:
                            raise
                    confirmed = {"active": False, "deleted_at": timestamp()} if self.resource == "products" else {"status": "inactive", "deleted_at": timestamp()}
                else:
                    result = await self.push(candidate)
                    source = result.get("data", result) if isinstance(result, dict) else None
                    if not isinstance(source, dict) or not source.get("id"):
                        raise HTTPException(502, f"POS did not confirm the {self.entity} identity")
                    confirmed = {key: source[key] for key in self.fields if key in source}
                    confirmed["pos_metadata"] = source if self.resource == "products" else {key: source[key] for key in (*self.fields, "id", "business_id", "tenant_id", "tenantId", "updated_at", "location") if key in source}
                    if self.resource == "outlets":
                        confirmed["address"] = source.get("address", source.get("location", ""))
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                safe = {"code": f"POS_{self.entity.upper()}_UPDATE_FAILED", "message": "The POS update could not be confirmed. Check the POS connection and retry."}
                if detail.get("status_code") in {400, 402, 403, 409, 422}:
                    safe["message"] = f"POS rejected this change. Refresh the {self.entity} and review it before submitting again."
                    await self.records.update_one({"id": product["id"], "pos_command.id": command["id"]},
                        {"$set": {"pos_sync_pending": False, "pos_sync_error": safe}})
                    raise RejectedSyncUpdate(safe) from exc
                await self.records.update_one({"id": product["id"], "pos_command.id": command["id"]}, {"$set": {"pos_sync_error": safe}})
                raise HTTPException(502, safe) from exc
            await self.records.update_one({"id": product["id"], "pos_command.id": command["id"]},
                {"$set": {**confirmed, "pos_sync_pending": False, "pos_sync_error": None, "pos_command.completed_at": timestamp(), "updated_at": timestamp()}})
            await self.record_audit(business_id, product, command)
            return {"status": "success", "error_count": 0}

    async def recover_forever(self):
        import asyncio, logging
        while True:
            try:
                async for product in self.records.find({"pos_sync_pending": True, "pos_command.version": 1}, {"_id": 0}):
                    await self.ensure_job(product)
            except Exception:
                logging.exception("Product intent recovery failed; durable intents remain stored")
            await asyncio.sleep(30)
