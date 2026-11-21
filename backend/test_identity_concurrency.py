import asyncio
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from fastapi import Response
from starlette.requests import Request
from motor.motor_asyncio import AsyncIOMotorClient

with patch.dict(os.environ, {'MONGO_URL': 'mongodb://127.0.0.1:27017', 'DB_NAME': 'identity_unit'}):
    import server


@unittest.skipUnless(os.environ.get('RUN_PROJECTION_DB_TESTS') == '1', 'Isolated MongoDB acceptance enabled explicitly')
class IdentityConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_admins_cannot_disable_each_other_and_leave_no_administrator(self):
        client = AsyncIOMotorClient('mongodb://127.0.0.1:27017', serverSelectionTimeoutMS=5000)
        name = 'identity_acceptance_' + uuid4().hex
        db = client[name]
        try:
            await db.users.insert_many([{'id': identity, 'email': identity + '@example.test', 'name': identity,
                'role': 'platform_admin', 'status': 'active', 'business_ids': []} for identity in ['a', 'b']])
            def request(identity):
                token = server.create_access_token(identity, identity + '@example.test', 0)
                return Request({'type': 'http', 'method': 'PUT', 'path': '/api/users',
                    'headers': [(b'authorization', ('Bearer ' + token).encode())]})
            with patch.object(server, 'db', db):
                outcomes = await asyncio.gather(
                    server.update_user('a', server.UserUpdate(status='inactive'), request('b'), Response()),
                    server.update_user('b', server.UserUpdate(status='inactive'), request('a'), Response()),
                    return_exceptions=True)
            self.assertEqual(sum(not isinstance(result, Exception) for result in outcomes), 1)
            self.assertEqual(await db.users.count_documents({'role': 'platform_admin', 'status': 'active'}), 1)
        finally:
            await client.drop_database(name)
            client.close()
