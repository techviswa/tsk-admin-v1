"""Link and import one verified local POS business without reprovisioning it."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import os
import sys
from urllib.parse import urlparse
from dotenv import dotenv_values
from bson import json_util

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / '.env.development.local')
assert urlparse(config['MONGO_URL']).hostname in {'localhost', '127.0.0.1'}
assert urlparse(config['POS_CORE_API_BASE_URL']).hostname in {'localhost', '127.0.0.1'}
assert config['DB_NAME'] == 'admincore_local'
os.environ.update({k: v for k, v in config.items() if v is not None})
os.environ.update(ENVIRONMENT='development', APP_ENV='development', NODE_ENV='development', RENDER='')
sys.path.insert(0, str(root / 'backend'))
import server


async def main():
    business_id, tenant_id = sys.argv[1:3]
    result = await server.pos_core_session_request('GET', f'admincore/tenants/{business_id}',
        extra_headers={'business_id': business_id, 'x-tenant-id': tenant_id}, use_login=False)
    data = result.get('data', result)
    business = data.get('business', data)
    assert business['id'] == business_id
    assert (business.get('tenantId') or business.get('tenant_id')) == tenant_id
    backups = root / 'recovery-backups'
    backups.mkdir(exist_ok=True)
    snapshot = {}
    for name in await server.db.list_collection_names():
        snapshot[name] = await server.db[name].find({}).to_list(None)
    backup = backups / ('admincore-before-link-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    backup.write_text(json_util.dumps(snapshot), encoding='utf-8')
    admin = await server.db.users.find_one({'email': config['ADMIN_EMAIL'], 'role': 'platform_admin'}, {'_id': 0})
    assert admin
    local_id = await server.sync_bridge_business(business, admin, datetime.now(timezone.utc).isoformat())
    print('Linked recovered local business:', local_id)
    for resource in ['outlets', 'products', 'staff-shifts', 'tables', 'bills', 'inventory', 'reports']:
        if resource not in server.POS_BRIDGE_RESOURCES:
            continue
        result = await server.sync_pos_bridge_resource_for_system(resource, local_id, admin)
        print(resource, result.get('status'), result.get('count'), 'errors:', result.get('error_count'))
        if result.get('error_count'):
            raise RuntimeError('Import requires review: ' + resource)
    server.client.close()


asyncio.run(main())
