"""Operator-visible queue health without job payloads, credentials or customer data."""
from datetime import datetime, timezone, timedelta

QUEUES = ('pos_change_jobs', 'pos_profile_jobs', 'pos_product_jobs', 'pos_outlet_jobs', 'pos_provisioning_jobs')


async def queue_health(db, stale_seconds=900):
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
    queues = {}
    for name in QUEUES:
        rows = await db[name].aggregate([{'$group': {'_id': '$status', 'count': {'$sum': 1}}}]).to_list(30)
        counts = {row['_id'] or 'unknown': row['count'] for row in rows}
        stale = await db[name].count_documents({'status': {'$in': ['pending', 'running', 'retrying']},
                                               '$or': [{'updated_at': {'$lt': cutoff}}, {'updated_at': {'$exists': False}, 'created_at': {'$lt': cutoff}}]})
        queues[name] = {'counts': counts, 'stale_active': stale, 'failed': counts.get('failed', 0)}
    return {'healthy': not any(row['stale_active'] or row['failed'] for row in queues.values()),
            'stale_after_seconds': stale_seconds, 'queues': queues}
