"""AdminCore projections for the newer, POS-owned operational modules."""

OPERATIONS = {
    "tip-payouts": ("Tip Payouts", "staff", ["active", "void"]),
    "payslips": ("Payslips", "staff", ["paid", "unpaid"]),
    "loyalty-ledger": ("Loyalty Ledger", "customers", ["earn", "redeem", "reverse_earn", "reverse_redeem", "adjust", "expire"]),
    "gift-card-transactions": ("Gift Card Transactions", "customers", ["issue", "redeem", "refund", "void", "adjust"]),
    "marketing-delivery": ("Marketing Delivery", "customers", ["queued", "sending", "sent", "delivered", "read", "failed", "skipped"]),
    "attendance": ("Attendance", "staff", ["clocked_in", "completed"]),
    "tips": ("Tips", "staff", ["active", "void"]),
    "payroll": ("Payroll", "staff", ["draft", "finalized", "void"]),
    "customer-profiles": ("Customer Profiles & Loyalty", "customers", ["active", "anonymized"]),
    "gift-cards": ("Gift Cards", "customers", ["pending_payment", "active", "blocked", "void"]),
    "marketing": ("Marketing", "customers", ["draft", "scheduled", "sending", "completed", "cancelled", "active", "paused"]),
    "menu-pricing": ("Menu Pricing / Happy Hours", "products", ["active", "inactive"]),
    "guest-feedback": ("Guest Feedback", "analytics", ["received"]),
    "table-assistance": ("Table Assistance", "tables", ["pending", "accepted", "resolved", "cancelled", "expired"]),
}


def register_operations(resources, bridge, modules):
    for key in ("qr-ordering", "central-kitchen"):
        resources[key]["read_only"] = True
    for priority, (key, (label, module, statuses)) in enumerate(OPERATIONS.items(), 23):
        collection = "pos_" + key.replace("-", "_")
        resources[key] = {"priority": priority, "collection": collection, "label": label,
                          "statuses": statuses, "read_only": True}
        export = "loyalty-customers" if key == "customer-profiles" else key
        bridge[key] = {"endpoint": f"sync/export/{export}", "mode": "pos_admin",
                       "collection": collection, "pos_resource": key, "label": label}
        modules[key] = module


async def reconcile_snapshot(collection, *, business_id, scope, external_ids, now):
    """Hide absent source rows after a complete export; retain the last snapshot for audit."""
    await collection.update_many({
        "business_id": business_id, "pos_business_id": scope["business_id"],
        "pos_tenant_id": scope["tenant_id"], "pos_synced": True,
        "pos_external_id": {"$nin": external_ids},
        "$or": [{"updated_at": {"$lte": now}}, {"updated_at": {"$exists": False}}],
    }, {"$set": {"source_present": False, "source_removed_at": now}})
