"""Revoke missing source memberships without deleting identity or business history."""


async def reconcile_staff_memberships(users, business_id, scope, external_ids, cutoff):
    path = f'pos_links.{business_id}'
    query = {
        f'{path}.business_id': scope['business_id'],
        f'{path}.tenant_id': scope['tenant_id'],
        f'{path}.user_id': {'$nin': list(external_ids)},
        f'{path}.status': {'$ne': 'inactive'},
        'pos_creation_pending': {'$ne': True},
        '$or': [{f'{path}.last_seen_at': {'$lte': cutoff}},
                {f'{path}.last_seen_at': {'$exists': False}, 'updated_at': {'$lte': cutoff}}],
    }
    async for user in users.find(query, {'_id': 0}):
        link = user['pos_links'][business_id]
        changes = {f'{path}.status': 'inactive', f'{path}.source_present': False,
                   f'{path}.source_removed_at': cutoff}
        if user.get('business_ids') == [business_id]:
            changes['status'] = 'inactive'
        await users.update_one({'id': user['id'], path: link, 'updated_at': user.get('updated_at')},
                               {'$set': changes, '$inc': {'token_version': 1}})
