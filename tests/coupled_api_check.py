"""Run real AdminCore/POS APIs and workers against fresh, isolated databases.

Usage: python tests/coupled_api_check.py --pos-root PATH
Windows: creates a private PostgreSQL cluster under ignored recovery-backups.
CI: pass --database-url for a disposable database named coupled_*.
No existing application configuration or business database is modified.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import requests
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def eventually(check, description, seconds=90):
    end, last = time.monotonic() + seconds, None
    while time.monotonic() < end:
        try:
            result = check()
            if result:
                return result
        except (requests.RequestException, AssertionError, KeyError) as error:
            last = type(error).__name__
        time.sleep(.5)
    raise AssertionError(f'Timed out: {description}; last failure: {last}. Inspect the isolated run logs.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pos-root', type=Path, required=True)
    parser.add_argument('--database-url')
    parser.add_argument('--pos-suite', action='store_true', help='Run the full POS release suite inside the disposable database first')
    parser.add_argument('--offline-browser', action='store_true', help='Verify the built POS in headless Chrome using this disposable database')
    parser.add_argument('--billing-panels-only', action='store_true', help='Run settlement and QR panel API acceptance in disposable databases')
    parser.add_argument('--business-profile-only', action='store_true', help='Run business profile round-trip acceptance in disposable databases')
    parser.add_argument('--postgres-bin', type=Path, default=Path('C:/Program Files/PostgreSQL/18/bin'))
    args = parser.parse_args()
    pos_root = args.pos_root.resolve()
    assert (pos_root / 'backend/src/server.js').is_file(), 'POS source folder not found'
    run_id = uuid4().hex
    output = ROOT / 'recovery-backups' / ('coupled-' + run_id)
    output.mkdir(parents=True)
    hidden = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    children, logs, cluster_started = [], [], False
    postgres = None
    mongo_name = 'admincore_coupled_' + run_id
    mongo = MongoClient(os.environ.get('COUPLED_MONGO_URL', 'mongodb://127.0.0.1:27017'), serverSelectionTimeoutMS=5000)
    database = mongo[mongo_name]
    http = requests.Session()
    http.trust_env = False
    env = os.environ.copy()
    admin_port, pos_port = port(), port()
    admin_url, pos_url = f'http://127.0.0.1:{admin_port}', f'http://127.0.0.1:{pos_port}'
    api_python = getattr(sys, '_base_executable', sys.executable) if os.name == 'nt' else sys.executable
    bridge_key, password = secrets.token_hex(32), secrets.token_hex(24)

    def run(command, *, cwd=ROOT, process_env=None):
        # Capture output: database connection strings and test secrets stay in ignored logs.
        with (output / 'setup.log').open('a', encoding='utf8') as log:
            subprocess.run(command, cwd=cwd, env=process_env or env, stdout=log, stderr=log, check=True, **hidden)

    def start(command, cwd, process_env, name):
        log = (output / (name + '.log')).open('w', encoding='utf8')
        logs.append(log)
        child = subprocess.Popen(command, cwd=cwd, env=process_env, stdout=log, stderr=log,
                                 start_new_session=os.name != 'nt', **hidden)
        children.append(child)
        return child

    def stop(child):
        if child.poll() is not None:
            return
        if os.name == 'nt':
            # API children use the real interpreter, not the Windows venv launcher.
            # Its creation handle identifies the worker without enumerating other processes.
            child.terminate()
        else:
            os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)

    def admin_stopped():
        try:
            http.get(admin_url + '/api/health', timeout=1)
            return False
        except requests.ConnectionError:
            return True

    try:
        url = args.database_url
        if url:
            parsed = urlparse(url)
            assert parsed.hostname in {'localhost', '127.0.0.1', 'postgres'}, 'Disposable local/CI database required'
            assert parsed.path.startswith('/coupled_'), 'Refusing migrations outside a coupled_* disposable database'
        else:
            assert os.name == 'nt', 'Pass --database-url for a disposable coupled_* database on Linux'
            cluster = (output / 'postgres').resolve()
            assert cluster.is_relative_to((ROOT / 'recovery-backups').resolve())
            pwfile = output / 'postgres-password.txt'
            pwfile.write_text(password, encoding='ascii')
            pg_port = port()
            run([str(args.postgres_bin / 'initdb.exe'), '-D', str(cluster), '-U', 'coupled_runner', '--pwfile=' + str(pwfile), '--auth=scram-sha-256', '--encoding=UTF8', '--no-locale'])
            pg_log = (output / 'postgres.log').open('w', encoding='utf8')
            logs.append(pg_log)
            postgres = subprocess.Popen([str(args.postgres_bin / 'postgres.exe'), '-D', str(cluster), '-p', str(pg_port), '-h', '127.0.0.1'], stdout=pg_log, stderr=pg_log, **hidden)
            cluster_started = True
            eventually(lambda: subprocess.run([str(args.postgres_bin / 'pg_isready.exe'), '-h', '127.0.0.1', '-p', str(pg_port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden).returncode == 0, 'isolated PostgreSQL readiness', 30)
            pg_env = {**env, 'PGPASSWORD': password}
            run([str(args.postgres_bin / 'createdb.exe'), '-h', '127.0.0.1', '-p', str(pg_port), '-U', 'coupled_runner', 'coupled_' + run_id], process_env=pg_env)
            url = f'postgresql://coupled_runner:{password}@127.0.0.1:{pg_port}/coupled_{run_id}?schema=public&connection_limit=5&connect_timeout=30&pool_timeout=30'
        pos_env = {**env, 'NODE_ENV': 'test', 'PORT': str(pos_port), 'DATABASE_URL': url,
                   'SYSTEM_DATABASE_URL': '', 'TENANT_DATABASE_URL': '', 'ADMINCORE_ENABLED': 'true',
                   'ADMINCORE_API_BASE_URL': admin_url, 'ADMINCORE_API_KEY': bridge_key,
                   'ADMINCORE_SYNC_WEBHOOK_URL': admin_url + '/api/pos-bridge/sync-status',
                   'POS_BASE_URL': pos_url, 'JWT_SECRET': secrets.token_hex(32), 'MARKETING_WORKER': 'off'}
        run(['node', 'node_modules/prisma/build/index.js', 'migrate', 'deploy'], cwd=pos_root / 'backend', process_env=pos_env)
        if args.billing_panels_only:
            run(['node', str(ROOT / 'tests/coupled_fixture.mjs'), str(pos_root)], cwd=pos_root, process_env=pos_env)
            run(['node', str(ROOT / 'tests/coupled_fixture.mjs'), str(pos_root), 'billing-panels'], cwd=pos_root, process_env=pos_env)
            evidence = {'result': 'passed', 'run_id': run_id, 'billing_panels': 'passed',
                        'scope': '105 scoped/searchable settlements, immutable closure/replay, 25 QR requests, outlet-scoped bill history/search/filter/paging and waiter-owned history/payment; disposable databases only'}
            (output / 'result.json').write_text(json.dumps(evidence, indent=2), encoding='utf8')
            print(json.dumps(evidence))
            return
        if args.pos_suite:
            suite_env = {**pos_env, 'ADMINCORE_ENABLED': 'false', 'ADMINCORE_API_BASE_URL': '', 'ADMINCORE_SYNC_WEBHOOK_URL': ''}
            run(['node', 'prisma/seed.js'], cwd=pos_root / 'backend', process_env=suite_env)
            run(['node', 'scripts/deploy-check.mjs'], cwd=pos_root / 'backend', process_env=suite_env)
            run(['node', 'scripts/product-command-tests.mjs'], cwd=pos_root / 'backend', process_env=suite_env)
            run(['node', 'scripts/outlet-command-tests.mjs'], cwd=pos_root / 'backend', process_env=suite_env)
            run(['node', '--test', 'scripts/operational-notification-tests.mjs'], cwd=pos_root / 'backend', process_env=suite_env)
        business_id, tenant_id = 'coupled-business', 'coupled-tenant'
        if not args.business_profile_only:
            run(['node', 'scripts/customer-privacy-tests.mjs'], cwd=pos_root / 'backend',
                process_env={**pos_env, 'ADMINCORE_ENABLED': 'false'})
            run(['node', 'scripts/export-snapshot-tests.mjs'], cwd=pos_root / 'backend',
                process_env={**pos_env, 'ADMINCORE_ENABLED': 'false'})
            run(['node', '--test', 'scripts/customer-export-tests.mjs'], cwd=pos_root / 'backend', process_env=pos_env)
            run(['node', 'scripts/multi-till-tests.mjs'], cwd=pos_root / 'backend',
                process_env={**pos_env, 'ADMINCORE_ENABLED': 'false'})
        if args.offline_browser:
            run(['node', 'backend/scripts/offline-till-tests.mjs'], cwd=pos_root,
                process_env={**pos_env, 'ADMINCORE_ENABLED': 'false'})
        if not args.business_profile_only:
            run(['node', str(ROOT / 'tests/run_pos_credential_check.mjs'), str(pos_root)], process_env=pos_env)
        run(['node', str(ROOT / 'tests/coupled_fixture.mjs'), str(pos_root)], cwd=pos_root, process_env=pos_env)
        run(['node', str(ROOT / 'tests/coupled_fixture.mjs'), str(pos_root), 'billing-panels'], cwd=pos_root, process_env=pos_env)
        from recovery_checks import verify_recovery
        recovery = None if args.business_profile_only else verify_recovery(url, output, args.postgres_bin, os.environ.get('COUPLED_MONGO_URL', 'mongodb://127.0.0.1:27017'))
        start(['node', 'backend/src/server.js'], pos_root, pos_env, 'pos')
        eventually(lambda: http.get(pos_url + '/api/admincore/health', timeout=2).ok, 'POS API readiness')
        admin_env = {**env, 'MONGO_URL': os.environ.get('COUPLED_MONGO_URL', 'mongodb://127.0.0.1:27017'),
                     'DB_NAME': mongo_name, 'JWT_SECRET': secrets.token_hex(32), 'SEED_DEMO_DATA': 'false',
                     'ADMIN_EMAIL': 'coupled-admin@example.test', 'ADMIN_PASSWORD': password,
                     'POS_CORE_API_BASE_URL': pos_url, 'POS_CORE_API_KEY': bridge_key,
                     'ADMINCORE_API_KEY': bridge_key, 'APP_ENV': 'development', 'ENVIRONMENT': 'development',
                     'POS_RECONCILE_INTERVAL_SECONDS': '60'}
        if os.name == 'nt':
            admin_env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(Path(sys.prefix) / 'Lib/site-packages'), env.get('PYTHONPATH', '')]))
        admin_command = [api_python, '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', str(admin_port)]
        admin = start(admin_command, ROOT / 'backend', admin_env, 'admin')
        eventually(lambda: http.get(admin_url + '/api/health', timeout=2).ok, 'AdminCore readiness', 120)
        database.businesses.insert_one({'id': 'local-coupled', 'name': 'Coupled fixture', 'plan': 'business', 'status': 'active',
                                        'pos_external_id': business_id, 'pos_tenant_id': tenant_id, 'pos_provisioning_status': 'ready'})
        database.business_modules.insert_one({'id': 'products-module', 'business_id': 'local-coupled', 'module_slug': 'products', 'enabled': True})
        for module in ['staff', 'customers']:
            database.business_modules.insert_one({'id': module + '-module', 'business_id': 'local-coupled', 'module_slug': module, 'enabled': True})
        session = requests.Session()
        session.trust_env = False
        session.headers['X-Requested-With'] = 'XMLHttpRequest'
        login = session.post(admin_url + '/api/auth/login', json={'email': 'coupled-admin@example.test', 'password': password}, timeout=10)
        login.raise_for_status()
        login_data = login.json()
        token = login_data.get('token') or login_data.get('access_token')
        if token:
            session.headers['Authorization'] = 'Bearer ' + token
        # Profile writes must survive a different process, synchronize both ways,
        # and reject stale edits without rewriting any financial/business policy.
        bridge_headers = {'x-admincore-api-key': bridge_key}
        profile_url = pos_url + '/api/admincore/business-profile/' + business_id
        def pos_staff(email):
            staff_http = requests.Session()
            staff_http.trust_env = False
            logged_in = staff_http.post(pos_url + '/api/auth/login', json={'email': email, 'password': 'panel-fixture-password-123', 'business_id': business_id}, timeout=10)
            logged_in.raise_for_status()
            staff_payload = logged_in.json().get('data', logged_in.json())
            staff_http.headers['x-cf-session-id'] = staff_payload['session_id']
            return staff_http
        pos_owner = pos_staff('panels@example.test')
        pos_waiter = pos_staff('stock-waiter@example.test')
        profile = http.get(profile_url, params={'tenant_id': tenant_id}, headers=bridge_headers, timeout=10)
        profile.raise_for_status()
        original = profile.json()['data']
        changed = pos_owner.put(pos_url + '/api/businesses/profile', json={
            'tenant_id': tenant_id, 'expected_updated_at': original['updated_at'], 'shopName': 'Synchronized shop',
            'gst': 'TEST-GST', 'address': 'Fixture address', 'phone': '+910000000000', 'footer': 'Fixture footer'}, timeout=15)
        changed.raise_for_status()
        confirmed_profile = changed.json().get('data', changed.json())
        waiter_profile = pos_waiter.get(pos_url + '/api/businesses/profile', timeout=10)
        waiter_profile.raise_for_status()
        assert waiter_profile.json()['shopName'] == 'Synchronized shop'
        denied = pos_waiter.put(pos_url + '/api/businesses/profile', json={'shopName': 'Waiter edit', 'expected_updated_at': confirmed_profile['updated_at']}, timeout=10)
        assert denied.status_code == 403
        original_account = pos_owner.get(pos_url + '/api/auth/me', timeout=10).json()['data']
        account_update = pos_owner.put(pos_url + '/api/staff/me/profile', json={'name': 'Updated cashier'}, timeout=10)
        account_update.raise_for_status()
        saved_account = account_update.json()['data']
        assert saved_account['email'] == original_account['email'] and saved_account['role'] == original_account['role']
        assert saved_account['id'] == original_account['id'] and saved_account['business_id'] == business_id
        for invalid_name in [' ', [], 'x' * 121]:
            invalid = pos_owner.put(pos_url + '/api/staff/me/profile', json={'name': invalid_name}, timeout=10)
            assert invalid.status_code == 400
        eventually(lambda: database.users.find_one({'pos_external_id': saved_account['id'], 'name': 'Updated cashier', 'business_ids': 'local-coupled'}), 'personal account name reaches AdminCore without renaming the business', 120)
        synced_business = eventually(lambda: database.businesses.find_one({'id': 'local-coupled', 'name': 'Synchronized shop', 'pos_profile.phone': '+910000000000'}), 'business profile and contacts reach AdminCore', 120)
        assert synced_business['plan'] == 'business' and synced_business['status'] == 'active'
        stale = http.put(profile_url, headers=bridge_headers, json={'tenant_id': tenant_id, 'expected_updated_at': original['updated_at'], 'shopName': 'Stale shop'}, timeout=10)
        assert stale.status_code == 409, stale.text
        wrong_tenant = http.put(profile_url, headers=bridge_headers, json={'tenant_id': 'another-tenant', 'expected_updated_at': confirmed_profile['updated_at'], 'shopName': 'Wrong tenant'}, timeout=10)
        assert wrong_tenant.status_code == 403
        renamed = session.put(admin_url + '/api/businesses/local-coupled', json={'name': 'Admin confirmed shop', 'expected_source_updated_at': synced_business['pos_source_updated_at']}, timeout=20)
        renamed.raise_for_status()
        source_profile = http.get(profile_url, params={'tenant_id': tenant_id}, headers=bridge_headers, timeout=10).json()['data']
        assert source_profile['shopName'] == 'Admin confirmed shop' and source_profile['address'] == 'Fixture address'
        assert source_profile['phone'] == '+910000000000' and source_profile['footer'] == 'Fixture footer'
        assert http.get(profile_url, params={'tenant_id': tenant_id}, timeout=10).status_code == 401
        print('Business profile: persistent contacts, POS-to-AdminCore sync, AdminCore-to-POS rename, stale-edit and tenant guards passed', flush=True)
        if args.business_profile_only:
            evidence = {'result': 'passed', 'run_id': run_id, 'business_profile_roundtrip': 'passed',
                'checks': ['POS Settings API write persists', 'waiter sees saved business name but cannot edit settings', 'durable business/contact export reaches AdminCore', 'personal account name reaches AdminCore without changing email or role', 'invalid account names rejected', 'AdminCore rename reaches POS', 'stale revision rejected', 'wrong tenant and unsigned bridge rejected', 'plan and lifecycle preserved'],
                'business_data_scope': 'disposable databases only'}
            (output / 'result.json').write_text(json.dumps(evidence, indent=2), encoding='utf8')
            print(json.dumps(evidence), flush=True)
            return
        payload = {'business_id': 'local-coupled', 'name': 'Durable fixture', 'price': 10.25, 'stock': 5, 'category': 'Test', 'request_id': run_id}
        def submit_twice(path, body):
            def submit(_):
                with requests.Session() as worker_http:
                    worker_http.trust_env = False
                    return worker_http.post(admin_url + path, json=body, headers=dict(session.headers), cookies=session.cookies.get_dict(), timeout=10)
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(submit, range(2)))
            assert all(response.status_code in {200, 202} for response in responses), [response.status_code for response in responses]
            assert responses[0].json()['id'] == responses[1].json()['id'], 'Concurrent replay created distinct local identities'
            return responses[0]
        created = submit_twice('/api/products', payload)
        assert created.status_code in {200, 202}, f'Product create returned {created.status_code}: {created.text[:250]}'
        product_id = created.json()['id']
        def confirmed():
            row = database.products.find_one({'id': product_id})
            return row if row and not row.get('pos_sync_pending') and row.get('pos_external_id') else None
        first = eventually(confirmed, 'actual product worker delivery')
        outlet_payload = {'name': 'Durable fixture outlet', 'code': 'FIXTURE-OUTLET', 'request_id': run_id}
        created_outlet = submit_twice('/api/outlets/business/local-coupled', outlet_payload)
        outlet_id = created_outlet.json()['id']
        def outlet_confirmed():
            row = database.outlets.find_one({'id': outlet_id})
            return row if row and not row.get('pos_sync_pending') and row.get('pos_external_id') else None
        first_outlet = eventually(outlet_confirmed, 'actual outlet worker delivery')
        assert database.outlets.count_documents({'id': outlet_id}) == 1
        assert database.products.count_documents({'id': product_id}) == 1
        headers = {'x-admincore-api-key': bridge_key, 'business_id': business_id, 'x-business-id': business_id, 'tenant_id': tenant_id, 'x-tenant-id': tenant_id}
        replay = session.post(admin_url + '/api/products', json=payload, timeout=10)
        assert replay.ok and replay.json()['id'] == product_id
        assert database.products.count_documents({'create_request_hash': first['create_request_hash']}) == 1
        # Hold this fixture's import lock so the update cannot finish before the restart.
        database.pos_import_leases.update_one({'_id': 'import:local-coupled:products'}, {'$set': {
            'owner': 'coupled-fixture', 'until': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()}}, upsert=True)
        database.pos_import_leases.update_one({'_id': 'import:local-coupled:outlets'}, {'$set': {
            'owner': 'coupled-fixture', 'until': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()}}, upsert=True)
        stale_outlet = session.put(admin_url + '/api/outlets/' + outlet_id, json={'name': 'Must not overwrite', 'expected_updated_at': '2000-01-01T00:00:00Z'}, timeout=10)
        assert stale_outlet.status_code == 409
        assert not database.outlets.find_one({'id': outlet_id})['pos_sync_pending']
        changed_outlet = session.put(admin_url + '/api/outlets/' + outlet_id, json={'name': 'Confirmed outlet after restart', 'expected_updated_at': first_outlet['pos_metadata']['updated_at']}, timeout=10)
        assert changed_outlet.status_code == 202, changed_outlet.text[:250]
        changed = session.put(admin_url + '/api/products/' + product_id, json={'price': 12.50, 'expected_updated_at': first['pos_metadata']['updated_at']}, timeout=10)
        assert changed.status_code == 202, changed.text[:250]
        # Restart the real API while a durable command is queued; its new worker must finish it.
        stop(admin)
        eventually(admin_stopped, 'old AdminCore API and worker must actually stop', 20)
        queued = database.products.find_one({'id': product_id})
        assert queued['pos_sync_pending'] and queued['price'] == 10.25, 'Restart check must begin with an unconfirmed command'
        queued_outlet = database.outlets.find_one({'id': outlet_id})
        assert queued_outlet['pos_sync_pending'] and queued_outlet['name'] == 'Durable fixture outlet'
        database.pos_import_leases.update_one({'_id': 'import:local-coupled:products', 'owner': 'coupled-fixture'},
                                               {'$set': {'until': datetime.now(timezone.utc).isoformat()}})
        database.pos_import_leases.update_one({'_id': 'import:local-coupled:outlets', 'owner': 'coupled-fixture'},
                                               {'$set': {'until': datetime.now(timezone.utc).isoformat()}})
        start(admin_command, ROOT / 'backend', admin_env, 'admin-restarted')
        eventually(lambda: http.get(admin_url + '/api/health', timeout=2).ok, 'restarted AdminCore', 120)
        final = eventually(lambda: (row if (row := confirmed()) and row.get('price') == 12.5 else None), 'durable update after restart')
        assert final['pos_external_id'] == first['pos_external_id']
        final_outlet = eventually(lambda: (row if (row := outlet_confirmed()) and row.get('name') == 'Confirmed outlet after restart' else None), 'durable outlet update after restart')
        assert final_outlet['pos_external_id'] == first_outlet['pos_external_id']
        # Source-only write must be reconciled by unattended workers, with no page requesting a refresh.
        source = http.post(pos_url + '/api/admincore/products', headers=headers,
                               json={'name': 'POS-only fixture', 'price': 7, 'stock': 2}, timeout=10)
        assert source.ok, source.text[:250]
        source_data = source.json()
        source_id = source_data.get('data', source_data)['id']
        # Exercise signed continuation over HTTP, beyond service-level cursor tests.
        export_params = {'limit': 1}
        exported_ids = set()
        while True:
            exported = http.get(pos_url + '/api/sync/export/products', headers=headers, params=export_params, timeout=10)
            assert exported.ok, exported.text[:250]
            exported_body = exported.json()
            if 'items' not in exported_body and isinstance(exported_body.get('data'), dict):
                exported_body = exported_body['data']
            for row in exported_body['items']:
                assert row['id'] not in exported_ids, 'HTTP cursor repeated a product'
                exported_ids.add(row['id'])
            paging = exported_body['meta']['pagination']
            assert paging['strategy'] == 'bounded-id-v1'
            if not paging['has_more']:
                break
            assert len(exported_ids) < 20, 'Fixture export failed to terminate'
            export_params = {'limit': 1, 'offset': paging['next_offset'], 'cursor': paging['next_cursor']}
        assert {first['pos_external_id'], source_id}.issubset(exported_ids)
        invalid_page = http.get(pos_url + '/api/sync/export/products', headers=headers,
                                params={'limit': 1, 'offset': 1}, timeout=10)
        assert invalid_page.status_code == 409, 'Unsigned continuation must be rejected'
        eventually(lambda: database.products.find_one({'pos_external_id': source_id, 'business_id': 'local-coupled'}), 'POS outbox/reconciler delivery without browser', 120)
        assert database.products.count_documents({'pos_external_id': first['pos_external_id']}) == 1
        for resource, source_id in [('tip-payouts', 'fixture-payout'), ('payslips', 'fixture-payslip'), ('loyalty-ledger', 'fixture-loyalty'), ('gift-card-transactions', 'fixture-card-transaction'), ('marketing-delivery', 'fixture-delivery')]:
            queued = session.post(admin_url + '/api/pos-bridge/queue/' + resource, params={'business_id': 'local-coupled'}, timeout=10)
            assert queued.ok, f'{resource}: {queued.status_code}: {queued.text[:200]}'
            eventually(lambda: database['pos_' + resource.replace('-', '_')].find_one({'pos_external_id': source_id, 'business_id': 'local-coupled'}), 'populated ' + resource + ' worker delivery', 120)
            response = session.get(admin_url + '/api/pos-admin/' + resource, params={'business_id': 'local-coupled', 'limit': 50}, timeout=10)
            assert response.ok, f'{resource}: {response.status_code}'
            assert source_id in response.text
            assert 'DO_NOT_EXPORT' not in response.text, 'Private source fields crossed the export allowlist'
        kot_queued = session.post(admin_url + '/api/pos-bridge/queue/kitchen-tickets', params={'business_id': 'local-coupled'}, timeout=10)
        assert kot_queued.ok
        kitchen = eventually(lambda: database.pos_kitchen_kot.find_one({'business_id': 'local-coupled', 'pos_external_id': 'fixture-kot'}), 'populated legacy kitchen ticket with verified tenant scope', 120)
        assert kitchen['pos_business_id'] == business_id and kitchen['pos_tenant_id'] == tenant_id
        for resource, collection in [('bills', 'pos_bills'), ('customers', 'pos_customers')]:
            queued = session.post(admin_url + '/api/pos-bridge/queue/' + resource, params={'business_id': 'local-coupled'}, timeout=10)
            assert queued.ok
            eventually(lambda: database[collection].find_one({'business_id': 'local-coupled', 'metadata.customer_phone' if resource == 'bills' else 'metadata.phone': '0000000000'}),
                       'pre-erasure private ' + resource + ' projection')
        run(['node', str(ROOT / 'tests/coupled_fixture.mjs'), str(pos_root), 'erase'], cwd=pos_root, process_env=pos_env)
        eventually(lambda: database.pos_privacy_erasures.find_one({'business_id': 'local-coupled', 'customer_id': 'fixture-customer'}),
                   'POS erasure outbox reaches AdminCore without browser', 120)
        def privacy_complete():
            row = database.pos_bills.find_one({'business_id': 'local-coupled', 'pos_external_id': 'fixture-private-bill'})
            return row if row and row.get('privacy_erased') and row['metadata'].get('customer_phone') is None else None
        erased_bill = eventually(privacy_complete, 'existing bill projection scrubbed', 120)
        assert erased_bill['amount'] == 123.45, 'Erasure changed financial evidence'
        assert erased_bill['metadata'].get('delivery_address') is None
        erased_kitchen = eventually(lambda: database.pos_kitchen_kot.find_one({'business_id': 'local-coupled', 'pos_external_id': 'fixture-kot', 'privacy_erased': True}), 'kitchen privacy erasure by order reference', 120)
        assert erased_kitchen['metadata']['customer_name'] == 'Erased customer'
        assert erased_kitchen['metadata'].get('notes') is None
        assert database.pos_customers.count_documents({'business_id': 'local-coupled', 'metadata.phone': '0000000000'}) == 0
        assert database.pos_customers.count_documents({'business_id': 'local-coupled', 'pos_external_id': {'$regex': '0000000000'}}) == 0
        # Exercise every business billing mode through AdminCore, its durable worker,
        # the authenticated bridge and real PostgreSQL-backed billing/order services.
        mode_path = admin_url + '/api/businesses/local-coupled/fulfillment-modes'
        for version, active_mode in enumerate(['TABLE', 'TOKEN', 'PICKUP', 'DELIVERY']):
            modes = {key: key == active_mode for key in ['TABLE', 'TOKEN', 'PICKUP', 'DELIVERY']}
            saved = session.put(mode_path, json={'version': version, 'modes': modes}, timeout=10)
            assert saved.status_code == 202, saved.text[:250]
            stale = session.put(mode_path, json={'version': version, 'modes': modes}, timeout=10)
            assert stale.status_code == 409, 'Stale AdminCore policy overwrote a newer version'
            eventually(lambda: session.get(mode_path, timeout=10).json().get('sync_status') == 'confirmed',
                       'billing policy confirmed by POS bridge', 120)
            overview = http.get(pos_url + '/api/admincore/tenants/' + business_id, headers=headers, timeout=10)
            assert overview.ok, overview.text[:250]
            value = overview.json()
            assert value.get('data', value)['fulfillment_modes'] == modes
            run(['node', str(ROOT / 'tests/coupled_fixture.mjs'), str(pos_root), 'billing-policy'], cwd=pos_root, process_env=pos_env)
        deleted = session.delete(admin_url + '/api/outlets/' + outlet_id, params={'expected_updated_at': final_outlet['pos_metadata']['updated_at']}, timeout=10)
        assert deleted.status_code == 202, deleted.text[:250]
        archived = eventually(lambda: database.outlets.find_one({'id': outlet_id, 'deleted_at': {'$type': 'string'}, 'pos_sync_pending': False}), 'confirmed historical outlet deactivation')
        assert archived['pos_external_id'] == first_outlet['pos_external_id']
        def revision(folder):
            return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=folder, capture_output=True, text=True, **hidden).stdout.strip()
        evidence = {'result': 'passed', 'checks': ['real product/outlet command delivery', 'concurrent create request replay', 'worker restart with demonstrably unconfirmed product and outlet commands', 'confirmed historical outlet deactivation', 'POS-only change reaches AdminCore without browser', 'signed HTTP export pagination and unsigned-continuation rejection', 'source identity remains unique', 'five populated operational transaction exports and private-field exclusion'],
                    'business_profile_roundtrip': 'passed', 'privacy_erasure_delivery': 'passed', 'billing_panels': 'passed', 'billing_modes_admincore_to_pos': 'passed', 'run_id': run_id, 'admin_revision': revision(ROOT), 'pos_revision': revision(pos_root),
                    'pos_release_suite': 'passed' if args.pos_suite else 'not_requested',
                    'offline_browser': 'passed' if args.offline_browser else 'not_requested',
                    'credential_create_and_update_replay': 'passed', 'multi_till_stock_recovery': 'passed',
                    'frozen_exports': 'passed', 'restore_proof': recovery,
                    'note': 'Local working-tree changes may be present. CI runs the recorded checkout revisions.'}
        (output / 'result.json').write_text(json.dumps(evidence, indent=2), encoding='utf8')
        print(json.dumps(evidence))
    except Exception as error:
        (output / 'result.json').write_text(json.dumps({'result': 'failed', 'run_id': run_id,
            'error_type': type(error).__name__, 'note': 'Detailed diagnostics remain in the ignored local run logs.'}, indent=2), encoding='utf8')
        raise
    finally:
        for child in reversed(children):
            stop(child)
        if cluster_started:
            if postgres.poll() is None:
                run([str(args.postgres_bin / 'pg_ctl.exe'), '-D', str(cluster), '-m', 'fast', '-w', 'stop'])
            postgres.wait(timeout=20)
        for log in logs:
            log.close()
        # Only this run's generated MongoDB namespace is eligible for cleanup.
        assert mongo_name == 'admincore_coupled_' + run_id
        mongo.drop_database(mongo_name)
        mongo.close()
        print('Isolated logs:', output)


if __name__ == '__main__':
    main()
