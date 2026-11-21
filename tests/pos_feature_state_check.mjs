import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

assert.ok(process.argv[2], 'Pass the POS project path');
const root = path.resolve(process.argv[2]);
const load = file => import(pathToFileURL(path.join(root, file)).href);
const { default: env } = await load('backend/src/config/env.js');
assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(new URL(env.database.url).hostname));
const { default: prisma } = await load('backend/src/database/prisma/client.js');
const { featureToggleService } = await load('backend/src/services/featureToggleService.js');
const { saasService } = await load('backend/src/core/saas/saas.service.js');
const id = `feature-check-${randomUUID()}`;
try {
  await saasService.upsertTenantFromAdminCore({business_id: id, tenant_id: id,
    plan: 'starter', name: 'Temporary feature fixture', enabled_features: ['kot', 'table-management'],
    limits: {staff: 8}});
  assert.equal(await featureToggleService.isFeatureEnabled('kot', id), true);
  await featureToggleService.setFeaturesForBusiness(id, []);
  assert.deepEqual(await featureToggleService.getEnabledFeaturesAsync(id), []);
  assert.equal(await featureToggleService.isFeatureEnabled('kot', id), false);
  await saasService.updateSubscription({businessId: id, payload: {enabled_features: ['kot']}});
  assert.equal(await featureToggleService.isFeatureEnabled('kot', id), true);
  assert.equal(await featureToggleService.isFeatureEnabled('table-management', id), false);
  console.log('POS feature grants, empty denial, and subscription reconciliation passed.');
} finally {
  await prisma.stateDocument.deleteMany({where: {key: `saas:${id}`}});
  await prisma.business.deleteMany({where: {id}});
  await prisma.$disconnect();
}
