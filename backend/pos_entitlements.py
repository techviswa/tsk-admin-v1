"""Translate AdminCore feature codes and limits into the POS bridge contract."""

FEATURES = {
    "billing": ("pos.basic", "billing.basic"),
    "products": ("products.basic",),
    "reports": ("reports.basic", "reports.standard", "reports.advanced"),
    "staff": ("users_roles.enabled", "staff.basic", "staff.attendance"),
    "table-management": ("tables.basic",),
    "kot": ("kot.basic", "kot.advanced"),
    "inventory": ("inventory.basic", "inventory.advanced"),
    "barcode": ("products.barcode",),
    "batch-tracking": ("inventory.batch_tracking",),
    "outlet-inventory-allocation": ("inventory.stock_transfer",),
    "outlet-purchase-orders": ("suppliers.basic", "suppliers.advanced"),
    "delivery-route-plan": ("delivery.advanced",),
}
LIMITS = {"outlets": "outlets.max", "staff": "users.max", "products": "products.max",
          "qr_tables": "qr_codes.max", "monthly_orders": "transactions.monthly"}
PLANS = {"free": "starter", "starter": "starter", "pro": "growth",
         "business": "enterprise", "enterprise": "enterprise"}
MODULES = {"billing": "pos", "products": "products", "reports": "reports",
           "staff": "staff", "table-management": "tables", "kot": "kitchen",
           "inventory": "inventory", "barcode": "products",
           "batch-tracking": "inventory", "outlet-inventory-allocation": "inventory",
           "outlet-purchase-orders": "suppliers", "delivery-route-plan": "delivery"}


def pos_entitlement_payload(entitlements):
    features = entitlements.get("features", {})
    limits = entitlements.get("limits", {})
    plan = entitlements.get("plan") or {}
    slug = str(plan.get("slug") or plan.get("code") or "free").lower()
    switches = entitlements.get("module_overrides", {})
    payload = {
        "plan": PLANS.get(slug, "starter"),
        "enabled_features": sorted(key for key, codes in FEATURES.items()
                                   if any(features.get(code) for code in codes)
                                   and switches.get(MODULES[key], True)),
        "limits": {key: None if limits[code] == "unlimited" else limits[code]
                   for key, code in LIMITS.items() if code in limits},
    }
    if entitlements.get("fulfillment_modes") is not None:
        payload["fulfillment_modes"] = entitlements["fulfillment_modes"]
    return payload
