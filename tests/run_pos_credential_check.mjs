import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const project = path.resolve(process.argv[2] || '');
assert.ok(process.argv[2], 'Pass the POS project directory');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const bridgeKey = 'cross-project-local-fixture-key-123456789';
Object.assign(process.env, {
  NODE_ENV: 'test', ADMINCORE_ENABLED: 'true', ADMINCORE_API_KEY: bridgeKey,
  ADMINCORE_API_BASE_URL: 'http://127.0.0.1:1', ADMINCORE_SYNC_WEBHOOK_URL: '',
});
const load = relative => import(pathToFileURL(path.join(project, relative)).href);
const { default: config } = await load('backend/src/config/env.js');
assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(new URL(config.database.url).hostname),
  'This fixture check must use a local POS test database');
const { default: app } = await load('backend/src/app.js');
const { default: prisma } = await load('backend/src/database/prisma/client.js');
const { usersService } = await load('backend/src/core/users/users.service.js');
const { verifyPassword } = await load('backend/src/core/auth/passwords.js');
const { jobQueue } = await load('backend/src/services/jobs/job-queue.js');
const { connectDatabase } = await load('backend/src/config/db.js');
const businessId = `credential-check-${randomUUID()}`;
const tenantId = `tenant-${businessId}`;
let listener, userId;
try {
  await connectDatabase();
  await prisma.business.create({ data: { id: businessId, tenantId, name: 'Temporary credential sync check' } });
  const outlet = await prisma.outlet.create({ data: { businessId, name: 'Fixture outlet', code: randomUUID() } });
  const secondOutlet = await prisma.outlet.create({ data: { businessId, name: 'Second fixture outlet', code: randomUUID() } });
  const user = await usersService.createUser({ businessId, tenantId,
    payload: { email: 'old@credential.test', name: 'Old name', role: 'Waiter', password: 'old-cross-project-fixture-password',
      permissions: ['waiter_view', 'attendance'], assigned_outlet_ids: [outlet.id, secondOutlet.id], profile_required: true } });
  userId = user.id;
  await usersService.createUser({ businessId, tenantId,
    payload: { email: 'taken@credential.test', name: 'Other fixture user', role: 'Waiter', password: 'other-fixture-password' } });
  listener = await new Promise(resolve => { const server = app.listen(0, '127.0.0.1', () => resolve(server)); });
  const fixture = { business_id: businessId, tenant_id: tenantId, user_id: userId,
    outlet_id: outlet.id, bridge_key: bridgeKey, base_url: `http://127.0.0.1:${listener.address().port}` };
  const originalLogin = await fetch(`${fixture.base_url}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'old@credential.test', password: 'old-cross-project-fixture-password', business_id: businessId }),
  });
  assert.equal(originalLogin.status, 200);
  const oldCookie = originalLogin.headers.get('set-cookie').split(';')[0];
  const outboxFilter = { type: 'admincore.notify-change', payload: { path: ['business_id'], equals: businessId } };
  const queuedBefore = await prisma.backgroundJob.count({ where: outboxFilter });
  jobQueue.start();
  const python = process.env.ADMINCORE_TEST_PYTHON || path.join(root, 'backend/.venv/Scripts/python.exe');
  const result = await promisify(execFile)(python, [path.join(root, 'tests/cross_project_credential_check.py')], {
    cwd: root, timeout: 300000, env: { ...process.env, CREDENTIAL_SYNC_FIXTURE: JSON.stringify(fixture) },
  });
  process.stdout.write(result.stdout);
  const saved = await prisma.user.findUniqueOrThrow({ where: { id: userId } });
  assert.equal(saved.email, 'updated@credential.test');
  assert.equal(saved.name, 'Updated name');
  assert.equal(saved.profileRequired, true, 'Credential updates must preserve profile requirements');
  assert.equal(verifyPassword('new-cross-project-fixture-password', saved.passwordHash), true);
  assert.equal(verifyPassword('old-cross-project-fixture-password', saved.passwordHash), false);
  assert.equal(await prisma.authSession.count({ where: { userId } }), 0);
  assert.equal(await prisma.backgroundJob.count({ where: outboxFilter }), queuedBefore + 2,
    'one durable notification must commit with each successful update/create; retries and rejected credentials must not create another');
  assert.equal(await prisma.user.count({ where: { businessId, email: 'created@credential.test' } }), 1);
  const oldSession = await fetch(`${fixture.base_url}/api/auth/me`, { headers: { Cookie: oldCookie } });
  assert.equal((await oldSession.json()).data, null);
  const login = password => fetch(`${fixture.base_url}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: saved.email, password, business_id: businessId }),
  });
  const oldLogin = await login('old-cross-project-fixture-password');
  assert.equal(oldLogin.status, 401);
  const newLogin = await login('new-cross-project-fixture-password');
  assert.equal(newLogin.status, 200);
  assert.ok(newLogin.headers.get('set-cookie'));
  const account = (await newLogin.json()).data.user;
  assert.equal(account.id, userId);
  assert.equal(account.business_id, businessId);
  assert.equal(account.role, 'Waiter', 'A credential update must not replace the POS role with the global Admin role');
  assert.deepEqual([...account.permissions].sort(), ['attendance', 'waiter_view']);
  assert.deepEqual([...account.assigned_outlet_ids].sort(), [outlet.id, secondOutlet.id].sort());
  const owner = await usersService.createUser({ businessId, tenantId, payload: {
    name: 'Fixture Owner', email: 'owner@credential.test', role: 'Owner', password: 'fixture-owner-password',
  } });
  const headers = { 'Content-Type': 'application/json', 'x-admincore-api-key': bridgeKey,
    'x-business-id': businessId, 'x-tenant-id': tenantId, 'x-admincore-actor-mode': 'tenant',
    'x-admincore-actor-id': owner.id, 'x-admincore-owner-required': 'true' };
  const duplicate = await fetch(`${fixture.base_url}/api/admincore/staff`, {
    method: 'POST', headers: { ...headers, 'x-admincore-actor-mode': 'platform' },
    body: JSON.stringify({ email: saved.email, name: 'Must not replace existing staff', role: 'Owner',
      password: 'must-not-replace-password', admincore_command_id: `create-${randomUUID()}` }),
  });
  assert.equal(duplicate.status, 409, 'A newly queued create must never upsert a concurrently created POS identity');
  assert.equal((await prisma.user.findUniqueOrThrow({ where: { id: userId } })).name, 'Updated name');
  const command = { name: 'Authorized change', admincore_command_id: `actor-${randomUUID()}` };
  const write = body => fetch(`${fixture.base_url}/api/admincore/staff/${userId}`, {
    method: 'PUT', headers, body: JSON.stringify(body),
  });
  assert.equal((await write(command)).status, 200);
  await prisma.user.update({ where: { id: owner.id }, data: { active: false } });
  assert.equal((await write(command)).status, 200, 'A committed receipt must remain confirmable after actor revocation');
  assert.equal((await write({ ...command, name: 'Unauthorized new change', admincore_command_id: `actor-${randomUUID()}` })).status, 403);
  assert.equal((await prisma.user.findUniqueOrThrow({ where: { id: userId } })).name, 'Authorized change');
  await prisma.user.update({ where: { id: owner.id }, data: { active: true } });
  const secondOwner = await usersService.createUser({ businessId, tenantId, payload: {
    name: 'Second Fixture Owner', email: 'second-owner@credential.test', role: 'Owner', password: 'fixture-owner-password',
  } });
  const competing = await Promise.allSettled([
    usersService.updateUser({ businessId, tenantId, userId: owner.id, payload: { active: false }, actor: secondOwner }),
    usersService.updateUser({ businessId, tenantId, userId: secondOwner.id, payload: { active: false }, actor: owner }),
  ]);
  assert.equal(competing.filter(result => result.status === 'fulfilled').length, 1, 'Two administrators must not deactivate the last two Owners concurrently');
  assert.equal(await prisma.user.count({ where: { businessId, active: true, role: { name: 'Owner' } } }), 1);
  const { admincoreChangeSyncService } = await load('backend/src/core/admincore/admincore-change-sync.service.js');
  const notify = admincoreChangeSyncService.notifyChange;
  try {
    admincoreChangeSyncService.notifyChange = async () => { throw new Error('fixture outbox failure'); };
    await assert.rejects(usersService.createUser({ businessId, tenantId, payload: {
      email: 'rollback@credential.test', name: 'Rollback', role: 'Waiter', password: 'fixture-rollback-password',
    } }), /fixture outbox failure/);
    assert.equal(await prisma.user.count({ where: { businessId, email: 'rollback@credential.test' } }), 0);
  } finally { admincoreChangeSyncService.notifyChange = notify; }
  console.log('Actor revocation, committed receipt confirmation, concurrent last-owner protection and atomic staff creation passed.');
  console.log('Cross-project credential sync passed: POS login accepts the new credentials, rejects the old password, and revokes the old session.');
} catch (error) {
  const snapshots = await prisma.backgroundJob.findMany({ where: { type: 'admincore.export-snapshot',
    payload: { path: ['business_id'], equals: businessId } }, select: { status: true, error: true } });
  console.error('Credential fixture snapshot diagnostics:', JSON.stringify(snapshots));
  throw error;
} finally {
  if (listener) await new Promise(resolve => listener.close(resolve));
  jobQueue.stop();
  while (jobQueue.busy) await new Promise(resolve => setTimeout(resolve, 20));
  if (userId) await prisma.authSession.deleteMany({ where: { userId } });
  await prisma.backgroundJob.deleteMany({ where: { type: 'admincore.notify-change', payload: { path: ['business_id'], equals: businessId } } });
  await prisma.adminCoreSyncLog.deleteMany({ where: { tenantId } });
  await prisma.business.deleteMany({ where: { id: businessId } });
  await prisma.$disconnect();
}
