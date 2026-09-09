# Production sync progress

Updated: 2026-09-10. This is a continuation record, not a production sign-off.

## Implemented in this change

- All AdminCore POS HTTP calls in this process share a paced request gate,
  including login, pagination, exports, and writes. A 429 closes the gate for
  the server's Retry-After duration (seconds or HTTP date).
- Provisioning and import jobs preserve their retry budget on rate limits.
  Structured errors and retry delays survive queue processing.
- Verified product and outlet links bypass full-list exports before updates.
  Responses must match the requested record, business, and tenant.
- Missing business links fail explicitly; header construction no longer creates
  and persists guessed POS identities.
- Sync All requires a selected business and durably queues one snapshot per
  resource. Concurrent submissions reuse active snapshot jobs. Completed and
  failed jobs can be queued again. Snapshot jobs do not repeat dependency exports.
- POS Bridge polls snapshot job states and displays the next retry time. Business
  switching discards outdated resource-load results.

## Verification

- Production config/guard suite: 29 tests passed.
- Sync batch/worker suite: 10 tests passed.
- Frontend production build: passed.
- POS public /health/ready returned {"ready":true} after an earlier timeout.
  This does not verify authenticated endpoints or deployed commit identity.
- Full unittest discovery hit a missing MONGO_URL in test_entitlements.py.
  That database integration suite has not run.
- The local POS checkout is at 52d9ae2 and already skips demo records during
  production seeding. No POS files were changed in this change.

## Still open, in priority order

1. Durable outbound user/profile updates. update_user still performs POS writes
   before committing AdminCore edits. Partial success across businesses needs
   persisted per-business progress and conflict handling. Passwords must remain
   hashed in any durable job, with no hashes exposed through job/status APIs.
2. Provisioning step recovery and authenticated tests against the deployed POS:
   business, owner, outlet, returned IDs, owner login, and a recoverable failed step.
3. Verify products, bills, payments, customers, inventory, reports, and staff for
   at least two isolated businesses, including edits, deletions, and replayed events.
4. Coalesce repeated webhook resource imports. The request gate is per process;
   multiple API replicas need a shared rate limiter or a single outbound worker.
5. Cross-process Mongo job integration tests, stale lease recovery, restart tests,
   and a retry policy for network/5xx failures beyond the existing bounded retries.
6. Auth/session, module/plan enforcement, role changes, and removed-business access
   regression tests. Current unit tests are not a full production audit.
7. Verify the deployed AdminCore/POS/frontend commits, Render logs, CORS/env, database
   indexes/backups, and authenticated UI workflows. Render credentials and current
   production application credentials are not available in this session.

Do not report any open item as completed based only on a build, a readiness
response, or a queued-status toast.
