"""Read models: complete aggregates and explicit source ownership."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException


def require_local_write(config, record=None, *, connected=False):
    record = record or {}
    if config.get("read_only") or connected or record.get("pos_synced") or record.get("pos_external_id"):
        raise HTTPException(409, {"code": "POS_OWNED_RESOURCE", "message": "Manage this operation in POS. AdminCore displays its synchronized records."})


def money(value):
    if hasattr(value, "to_decimal"):
        value = value.to_decimal()
    number = Decimal(str(value or 0))
    return float(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if number.is_finite() else None


async def summarize(collection, query, amount_expression=None):
    amount = {"$convert": {"input": amount_expression if amount_expression is not None else "$amount", "to": "decimal", "onError": None, "onNull": None if amount_expression is not None else 0}}
    invalid = {"$or": [{"$eq": [amount, None]}, {"$in": [{"$convert": {"input": amount, "to": "string", "onError": "NaN", "onNull": ""}}, ["NaN", "Infinity", "-Infinity"]]}]}
    result = await collection.aggregate([
        {"$match": query},
        {"$facet": {
            "totals": [{"$group": {"_id": None, "count": {"$sum": 1},
                "active": {"$sum": {"$cond": [{"$ne": [{"$ifNull": ["$active", True]}, False]}, 1, 0]}},
                "positive": {"$sum": {"$cond": [{"$gt": [amount, 0]}, 1, 0]}},
                "invalid": {"$sum": {"$cond": [invalid, 1, 0]}}}}],
            "currencies": [{"$group": {"_id": {"$ifNull": ["$currency", {"$ifNull": ["$metadata.currency", "unspecified"]}]}, "amount": {"$sum": {"$cond": [invalid, 0, amount]}}}}],
            "statuses": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}],
        }},
    ]).to_list(1)
    data = result[0] if result else {}
    totals = (data.get("totals") or [{}])[0]
    currencies = [{"currency": row["_id"], "amount": money(row["amount"])} for row in data.get("currencies", [])]
    invalid = totals.get("invalid", 0)
    if invalid:
        currencies = [{**row, "amount": None} for row in currencies]
    return {"total": totals.get("count", 0), "active": totals.get("active", 0), "positive": totals.get("positive", 0),
            "amount_total": None if invalid or len(currencies) > 1 else (currencies[0]["amount"] if currencies else 0),
            "amount_by_currency": currencies, "invalid_amounts": invalid,
            "status_counts": [{"status": row["_id"], "count": row["count"]} for row in data.get("statuses", [])]}


def bill_revenue_expression():
    gross = {"$convert": {"input": {"$ifNull": ["$metadata.total", "$amount"]}, "to": "decimal", "onError": None, "onNull": None}}
    refund = {"$convert": {"input": {"$ifNull": ["$metadata.refunded_amount", {"$ifNull": ["$metadata.metadata.refunded_amount", 0]}]}, "to": "decimal", "onError": None, "onNull": 0}}
    invalid = [{"$or": [{"$eq": [value, None]}, {"$in": [{"$convert": {"input": value, "to": "string", "onError": "NaN", "onNull": "NaN"}}, ["NaN", "Infinity", "-Infinity"]]}]} for value in (gross, refund)]
    return {"$cond": [{"$or": invalid}, None, {"$max": [0, {"$subtract": [gross, refund]}]}]}


def bill_revenue(row):
    source = row.get('metadata') or row
    if str(source.get('status') or row.get('status') or '').lower() in {'void', 'cancelled', 'canceled'}:
        return Decimal(0)
    try:
        gross = Decimal(str(source.get('total', row.get('amount', 0))))
        refund = Decimal(str(source.get('refunded_amount', (source.get('metadata') or {}).get('refunded_amount', 0)) or 0))
        if not gross.is_finite() or not refund.is_finite():
            raise ValueError()
        return max(Decimal(0), gross - refund)
    except Exception:
        raise HTTPException(409, 'A source invoice contains an invalid amount; the report was withheld') from None


async def payment_groups(collection, query):
    amount = {"$convert": {"input": "$amount", "to": "decimal", "onError": 0, "onNull": 0}}
    rows = await collection.aggregate([{"$match": query}, {"$facet": {
        "methods": [{"$group": {"_id": {"method": {"$ifNull": ["$metadata.payment_method", {"$ifNull": ["$payment_method", {"$ifNull": ["$metadata.method", "$category"]}]}]},
            "currency": {"$ifNull": ["$metadata.currency", {"$ifNull": ["$currency", "unspecified"]}]}}, "amount": {"$sum": amount}}}],
        "refunds": [{"$group": {"_id": {"$ifNull": ["$metadata.refund_status", {"$ifNull": ["$refund_status", "none"]}]}, "count": {"$sum": 1}}}],
    }}]).to_list(1)
    data = rows[0] if rows else {}
    return {"by_method": [{"method": row["_id"]["method"] or "unspecified", "currency": row["_id"]["currency"], "amount": money(row["amount"])} for row in data.get("methods", [])],
            "by_refund": [{"refund_status": row["_id"], "count": row["count"]} for row in data.get("refunds", [])]}


async def kitchen_groups(collection, query):
    result = await collection.aggregate([{"$match": query}, {"$facet": {
        "total": [{"$count": "count"}],
        "statuses": [{"$group": {"_id": {"$ifNull": ["$status", "pending"]}, "count": {"$sum": 1}}}],
        "chefs": [{"$group": {"_id": {"$ifNull": ["$chef_name", {"$ifNull": ["$owner_name", "Unassigned"]}]}, "count": {"$sum": 1}}}],
        "items": [{"$project": {"items": {"$objectToArray": {"$cond": [{"$eq": [{"$type": "$item_statuses"}, "object"]}, "$item_statuses", {}]}}}},
                  {"$unwind": "$items"}, {"$group": {"_id": "$items.v", "count": {"$sum": 1}}}],
    }}]).to_list(1)
    data = result[0] if result else {}
    return {"total_tickets": (data.get('total') or [{'count': 0}])[0]['count'],
            "by_status": [{"status": row['_id'], "count": row['count']} for row in data.get('statuses', [])],
            "by_chef": [{"chef": row['_id'], "tickets": row['count']} for row in data.get('chefs', [])],
            "by_item_status": [{"status": row['_id'], "count": row['count']} for row in data.get('items', [])]}


def report_filter(query, *, outlet_id=None, date_from=None, date_to=None):
    from datetime import datetime, timedelta
    result = dict(query)
    if isinstance(outlet_id, str) and outlet_id:
        result['outlet_id'] = outlet_id
    bounds = {}
    for value, key, days in ((date_from, '$gte', 0), (date_to, '$lt', 1)):
        if not isinstance(value, str) or not value:
            continue
        try:
            date = datetime.strptime(value, '%Y-%m-%d') + timedelta(days=days)
        except ValueError:
            raise HTTPException(400, 'Dates must use YYYY-MM-DD') from None
        bounds[key] = date.strftime('%Y-%m-%dT00:00:00')
    if date_from and date_to and isinstance(date_from, str) and isinstance(date_to, str) and date_from > date_to:
        raise HTTPException(400, 'The start date must not follow the end date')
    if bounds:
        result['created_at'] = bounds
    return result
