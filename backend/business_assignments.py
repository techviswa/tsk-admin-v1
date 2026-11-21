"""Recover references to deleted businesses without discarding verified POS links."""
from fastapi import HTTPException


async def split_business_ids(db, business_ids):
    ids = list(dict.fromkeys(business_ids or []))
    if not ids:
        return [], []
    rows = await db.businesses.find({"id": {"$in": ids}}, {"_id": 0, "id": 1}).to_list(len(ids))
    found = {row["id"] for row in rows}
    return [bid for bid in ids if bid in found], [bid for bid in ids if bid not in found]


async def recover_business_assignments(db, existing, changes, *, platform_admin):
    """Only admins may remove already stored orphans; new unknown IDs remain invalid.

    Return the original, still-existing businesses that need POS synchronization.
    Keep the original document intact for the worker's concurrency checks.
    """
    active, missing = await split_business_ids(db, existing.get("business_ids"))
    if not missing or not platform_admin:
        return active
    linked = [bid for bid in missing if (existing.get("pos_links") or {}).get(bid)]
    # Older profiles can have a global POS identity without per-business links.
    if existing.get("pos_external_id") and existing.get("pos_business_id") in missing:
        linked.append(existing["pos_business_id"])
    if linked:
        raise HTTPException(status_code=409, detail={
            "code": "BUSINESS_LINK_REPAIR_REQUIRED",
            "message": "A deleted business still has a verified POS user link. Restore its business mapping before editing this account.",
            "business_ids": list(dict.fromkeys(linked)),
        })
    submitted = changes.get("business_ids", existing.get("business_ids", []))
    changes["business_ids"] = list(dict.fromkeys(bid for bid in submitted if bid not in missing))
    return active
