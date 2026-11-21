"""Actual AdminCore API + durable Mongo worker against the local POS HTTP fixture."""
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.update(MONGO_URL="mongodb://127.0.0.1:27017", DB_NAME="admincore_test_unused",
                  ENVIRONMENT="test", NODE_ENV="test", RENDER="", JWT_SECRET="local-fixture-jwt-secret-12345678901234567890",
                  FRONTEND_URL="http://test", COOKIE_SECURE="false", COOKIE_SAMESITE="lax",
                  SEED_DEMO_DATA="false", ADMIN_EMAIL="fixture-admin@credential.test", ADMIN_PASSWORD="fixture-admin-password-123")
import server
from pos_profile_updates import PosProfileUpdates


async def main():
    fixture = json.loads(os.environ["CREDENTIAL_SYNC_FIXTURE"])
    assert urlparse(fixture["base_url"]).hostname in {"localhost", "127.0.0.1"}
    db_name = "admincore_credential_test_" + uuid.uuid4().hex
    assert re.fullmatch(r"admincore_credential_test_[a-f0-9]{32}", db_name)
    server.db = server.client[db_name]
    assert await server.db.users.count_documents({}) == 0
    server.POS_CORE_API_BASE_URL = fixture["base_url"]
    server.POS_CORE_API_KEY = fixture["bridge_key"]
    server.pos_profile_updates = PosProfileUpdates(server.db, server.push_admin_user_to_pos, server.create_audit_log, authorize=server.queued_pos_actor_headers)
    try:
        await server.seed_data()
        business_id, tenant_id = fixture["business_id"], fixture["tenant_id"]
        await server.db.businesses.insert_one({"id": business_id, "name": "Fixture", "plan": "starter", "status": "active",
            "pos_external_id": business_id, "pos_tenant_id": tenant_id})
        await server.seed_business_defaults(business_id)
        await server.db.outlets.insert_one({"id": "fixture-outlet", "business_id": business_id, "pos_external_id": fixture["outlet_id"],
            "pos_business_id": business_id, "pos_tenant_id": tenant_id, "pos_synced": True})
        original_password = "old-cross-project-fixture-password"
        await server.db.users.insert_one({"id": "fixture-user", "email": "old@credential.test", "name": "Old name", "role": "staff",
            "status": "active", "password_hash": server.hash_password(original_password), "business_ids": [business_id],
            "pos_links": {business_id: {"user_id": fixture["user_id"], "business_id": business_id, "tenant_id": tenant_id}}})
        password = "new-cross-project-fixture-password"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}) as api:
            login = await api.post('/api/auth/login', json={"email": os.environ['ADMIN_EMAIL'], "password": os.environ['ADMIN_PASSWORD']})
            assert login.status_code == 200, login.text
            result = await api.put('/api/users/fixture-user', json={"name": "Updated name", "email": "updated@credential.test", "password": password})
            assert result.status_code == 202, result.text
            queued = await server.db.pos_profile_jobs.find_one({"_id": "fixture-user"})
            assert queued["status"] == "pending"
            assert password not in str(queued)
            before = await server.db.users.find_one({"id": "fixture-user"})
            assert server.verify_password(original_password, before['password_hash'])
            real_audit = server.pos_profile_updates.audit
            failures = [True]
            async def interrupted_audit(*args, **kwargs):
                if failures.pop() if failures else False:
                    raise RuntimeError('fixture interruption after credential commit')
                return await real_audit(*args, **kwargs)
            server.pos_profile_updates.audit = interrupted_audit
            real_request = server.pos_core_session_request
            lose_ack = [True]
            async def lose_first_ack(method, endpoint, **kwargs):
                delivered = await real_request(method, endpoint, **kwargs)
                if method == 'PUT' and endpoint.startswith('admincore/staff/') and lose_ack:
                    lose_ack.pop()
                    raise server.HTTPException(502, 'Simulated connection loss after POS committed')
                return delivered
            server.pos_core_session_request = lose_first_ack
            assert (await server.pos_profile_updates.worker.run_once())["status"] == "retrying"
            # POS committed but its acknowledgement was lost. A newly opened session
            # must survive replay of the exact same durable credential command.
            source_client = httpx.AsyncClient(base_url=fixture['base_url'], trust_env=False)
            source_login = await source_client.post('/api/auth/login', json={'email': 'updated@credential.test', 'password': password, 'business_id': business_id})
            assert source_login.status_code == 200
            await server.db.pos_profile_jobs.update_one({"_id": "fixture-user"}, {"$set": {"run_after": datetime.now(timezone.utc).isoformat()}})
            assert (await server.pos_profile_updates.worker.run_once())["status"] == "retrying"
            assert (await source_client.get('/api/auth/me')).json()['data']['id'] == fixture['user_id'], 'Command replay revoked a new login'
            await source_client.post('/api/auth/logout')
            await source_client.aclose()
            server.pos_core_session_request = real_request
            await server.db.pos_profile_jobs.update_one({"_id": "fixture-user"}, {"$set": {"run_after": datetime.now(timezone.utc).isoformat()}})
            assert (await server.pos_profile_updates.worker.run_once())["status"] == "synced"
            saved = await server.db.users.find_one({"id": "fixture-user"})
            assert saved['email'] == 'updated@credential.test'
            assert server.verify_password(password, saved['password_hash'])
            assert saved['token_version'] == 1
            job = await server.db.pos_profile_jobs.find_one({"_id": "fixture-user"})
            assert job['status'] == 'synced' and job['event']['completed_business_ids'] == [business_id]
            conflict = await api.put('/api/users/fixture-user', json={"email": "taken@credential.test", "password": "rejected-fixture-password"})
            assert conflict.status_code == 202, conflict.text
            assert (await server.pos_profile_updates.worker.run_once())["status"] == "rejected"
            unchanged = await server.db.users.find_one({"id": "fixture-user"})
            assert unchanged['email'] == saved['email'] and unchanged['password_hash'] == saved['password_hash']
            assert unchanged['token_version'] == 1
            created = await api.post('/api/users', json={'email': 'created@credential.test', 'name': 'New fixture',
                'password': 'created-fixture-password', 'role': 'staff', 'business_ids': [business_id]})
            assert created.status_code == 202, created.text
            created_id = created.json()['id']
            pending_login = await api.post('/api/auth/login', json={'email': 'created@credential.test', 'password': 'created-fixture-password'})
            assert pending_login.status_code == 403, 'Unconfirmed source provisioning must not grant AdminCore access'
            lost_create_ack = [True]
            async def lose_create_ack(method, endpoint, **kwargs):
                delivered = await real_request(method, endpoint, **kwargs)
                if method == 'POST' and endpoint == 'admincore/staff' and lost_create_ack:
                    lost_create_ack.pop()
                    raise server.HTTPException(502, 'Simulated lost staff create acknowledgement')
                return delivered
            server.pos_core_session_request = lose_create_ack
            create_attempt = await server.pos_profile_updates.worker.run_once()
            assert create_attempt['status'] == 'retrying', create_attempt
            assert not lost_create_ack, create_attempt
            async with httpx.AsyncClient(base_url=fixture['base_url'], trust_env=False) as source:
                assert (await source.post('/api/auth/login', json={'email': 'created@credential.test',
                    'password': 'created-fixture-password', 'business_id': business_id})).status_code == 200
                await server.db.pos_profile_jobs.update_one({'_id': created_id}, {'$set': {'run_after': datetime.now(timezone.utc).isoformat()}})
                assert (await server.pos_profile_updates.worker.run_once())['status'] == 'synced'
                assert (await source.get('/api/auth/me')).json()['data']['email'] == 'created@credential.test', 'Create replay revoked the new staff session'
                await source.post('/api/auth/logout')
            server.pos_core_session_request = real_request
            assert not (await server.db.users.find_one({'id': created_id})).get('pos_creation_pending')
            assert (await api.post('/api/auth/login', json={'email': 'created@credential.test', 'password': 'created-fixture-password'})).status_code == 200
        print("Real AdminCore API and MongoDB job persistence passed: queued, synced, rejected; credential and session version committed atomically.")
    finally:
        # This UUID database is created exclusively by this harness, never a configured app database.
        await server.client.drop_database(db_name)
        server.client.close()


asyncio.run(main())
