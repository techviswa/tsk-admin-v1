import asyncio
from datetime import datetime, timezone
from bson import ObjectId

import server


async def make_business(plan_slug: str):
    now = datetime.now(timezone.utc).isoformat()
    business_id = f"test-ent-{ObjectId()}"
    await server.db.businesses.insert_one({"id": business_id, "name": "Entitlement Test", "slug": business_id, "type": "restaurant", "plan": plan_slug, "status": "active", "branding": {}, "created_at": now, "updated_at": now})
    await server.sync_system_modules()
    plan = await server.db.plans.find_one({"slug": plan_slug}, {"_id": 0})
    await server.db.subscriptions.insert_one({"id": str(ObjectId()), "business_id": business_id, "plan_id": plan["id"], "plan_slug": plan_slug, "status": "active", "billing_cycle": "monthly", "current_period_start": now, "current_period_end": now, "created_at": now, "updated_at": now})
    await server.seed_business_defaults(business_id)
    return business_id


async def cleanup(business_id: str):
    await server.db.businesses.delete_one({"id": business_id})
    for collection in [
        server.db.subscriptions,
        server.db.business_modules,
        server.db.business_addons,
        server.db.business_entitlement_overrides,
        server.db.business_limit_overrides,
        server.db.outlets,
    ]:
        await collection.delete_many({"business_id": business_id})


async def main():
    await server.sync_system_modules()
    business_id = await make_business("starter")
    other_business_id = await make_business("free")
    try:
        assert await server.has_feature(business_id, "kot.basic") is True
        assert await server.has_feature(business_id, "kot.advanced") is False
        assert await server.has_feature(other_business_id, "inventory.basic") is False
        await server.db.subscriptions.update_one(
            {"business_id": other_business_id},
            {"$set": {"plan_id": "stale-plan-id", "plan_slug": "free"}},
        )
        assert await server.has_feature(other_business_id, "billing.basic") is True

        free_inventory_module = await server.ensure_business_module_row(other_business_id, "inventory")
        assert free_inventory_module["enabled"] is False
        try:
            await server.require_business_module_enabled(other_business_id, "inventory")
            raise AssertionError("outside-plan disabled module should block access")
        except server.HTTPException as exc:
            assert exc.status_code == 403
            assert exc.detail["code"] == "FEATURE_NOT_INCLUDED"

        default_outlet = await server.ensure_default_outlet_for_business(business_id, sync_to_pos=False)
        assert default_outlet["name"] == "Main Outlet"
        assert default_outlet["business_id"] == business_id
        assert default_outlet["pos_business_id"] == business_id
        second_default = await server.ensure_default_outlet_for_business(business_id, sync_to_pos=False)
        assert second_default["id"] == default_outlet["id"]
        assert await server.db.outlets.count_documents({"business_id": business_id}) == 1

        addon = await server.db.addon_catalog.find_one({"code": "advanced_qr_ordering"}, {"_id": 0})
        await server.db.business_addons.insert_one({"id": str(ObjectId()), "business_id": business_id, "addon_id": addon["id"], "addon_code": addon["code"], "quantity": 1, "status": "active"})
        assert await server.has_feature(business_id, "qr.analytics") is True
        assert await server.get_limit(business_id, "qr_codes.max") == 600

        await server.db.business_entitlement_overrides.insert_one({"id": str(ObjectId()), "business_id": business_id, "feature_code": "integrations.api", "enabled": True})
        assert await server.has_feature(business_id, "integrations.api") is True

        await server.db.business_modules.update_one({"business_id": business_id, "module_slug": "kitchen"}, {"$set": {"enabled": False}}, upsert=True)
        try:
            await server.require_business_module_enabled(business_id, "kitchen")
            raise AssertionError("disabled module should block access")
        except server.HTTPException as exc:
            assert exc.status_code == 403

        restricted_user = {"role": "business_owner", "business_ids": [business_id]}
        try:
            await server.validate_business_access(restricted_user, other_business_id)
            raise AssertionError("user should not access another business")
        except server.HTTPException as exc:
            assert exc.status_code == 403
    finally:
        await cleanup(business_id)
        await cleanup(other_business_id)
    print("entitlement tests passed")


if __name__ == "__main__":
    asyncio.run(main())
