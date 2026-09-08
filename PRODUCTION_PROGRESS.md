# Production Hardening Progress

Scope: AdminCore and the sibling POS repository `viswa pos table error`.
Requested: implement the production audit findings and retain progress across continuations.

## Implemented Locally (2026-09-08)

- Bounded sequential Sync All, explicit skipped/failed results, in-process overlap guard and fatal-error stop.
- Explicit business AND tenant scope for bridge requests/imports; no inferred scope for unscoped rows.
- Native payment/customer/report export preservation and primary export pagination.
- Provisioning verifies POS business/outlet/owner IDs before readiness; pending/failed operational APIs and buttons blocked.
- Owner responses exclude Mongo ObjectId/password hash.
- Stable per-business POS user mappings for profile/credential updates; password changes revoke POS sessions.
- Correct product/inventory notification actions; reads no longer emit changes.
- Bill-change dependency sync and scoped deletion for supported resources.
- Persistent scoped payment intents with concurrency guards; unverified public confirmations blocked.
- Production configuration validation, stale frontend business/module response protection, hook warning fixes and additional CI tests.

## Final Local Verification

- 15 AdminCore production/configuration guards and 6 sync-batch tests passed.
- AdminCore frontend production build compiled successfully without lint warnings.
- POS deploy check passed: schema validation, retry/crash recovery, payment persistence/isolation/concurrency, notifications, pagination and endpoint smoke tests.
- Actual two-business cross-project test passed: provisioning readiness, login, profile/email/password updates, session revocation, product isolation, bills and dependent payments/customers/reports sync with matching business scope.
- Cross-project test invokes the change processor directly; it does NOT prove unattended delivery through deployed workers.
- Both repositories passed git diff --check (line-ending warnings only).
- Earlier isolated MongoDB entitlement tests and 9 provisioning tests passed.
- Changes remain uncommitted, unpushed and undeployed. Production readiness is NOT established.

Cross-project reproduction: run `node backend/scripts/cross-project-tests.mjs "<AdminCore backend path>"` from POS with dedicated local MongoDB on port 27019 and AdminCore requirements-test.txt installed. The harness removes its own prefixed test data.

## Remaining Verification and Work

- Make all relevant writes/event enqueue atomic and verify unattended worker recovery/deletes for every resource.
- Make Sync All durable/resumable across processes; current lock is process-local and timed-out HTTP threads may finish in the background.
- Add provisioning submission idempotency and recover failures between local creation and enqueue.
- Reconcile partially completed multi-business credential updates and revoke removed remote assignments.
- Audit remaining export caps and linked order/bill customer-total double counting.
- Implement verified payment provider integration if online payments are offered; public confirmation currently fails closed.

- Enforce pending provisioning readiness throughout business operations; verify retry/replay and partial failures.
- Expand tenant isolation tests across reads, writes, exports, credentials, bills and reports.
- Verify automatic change delivery, recovery, deletes and durable sync health.
- Audit module, role and entitlement enforcement, including concurrent usage limits.
- Complete business control center and create/edit workflows against current backend contracts.
- Resolve frontend hook warnings with behavior checks.
- Validate authentication, stable production secrets, environment validation and health checks.
- Expand AdminCore CI and run both projects' relevant production tests/builds.
- Verify deployed commits, live provisioning and cross-business synchronization.
- Verify database backups and restore, deployment configuration and monitoring with infrastructure access.

## Baseline

- Both repositories were clean at start on 2026-09-07.
- Prior audit: AdminCore frontend build and Python syntax passed; entitlement test was blocked by missing MONGO_URL.
- Prior audit: POS deploy check passed. These are baseline results, not validation of subsequent changes.
- Production readiness is not established by these local baseline checks.
