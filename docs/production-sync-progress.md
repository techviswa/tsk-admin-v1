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

1. Live verification of the implemented durable profile recovery. Conflicting
   concurrent edits currently fail explicitly and require reconciliation; they
   are not silently overwritten. Status APIs omit stored credential hashes.
2. Live verification of provisioning step recovery against the deployed POS:
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
   production application credentials require explicit approval before use.

Do not report any open item as completed based only on a build, a readiness
response, or a queued-status toast.

## Recovery follow-up

Implemented after the initial batch:

- Profile edits are durably queued with hashed credentials and per-business
  checkpoints. AdminCore applies the requested profile after POS steps complete.
  Retries skip completed businesses; conflicting later profile edits fail explicitly.
- Removed business assignments deactivate the corresponding POS account. Editing
  within a selected business preserves the user's other memberships.
- User rows display pending/retrying/failed state. Sync to POS retries failed jobs.
- Provisioning persists tenant/owner checkpoints, rejects overlapping jobs, bounds
  worker execution, fences job updates with a lease, and handles final-attempt crashes.
- Provisioning verifies the owner's link for the specific business, rather than
  trusting a global POS user ID that may belong to another business.
- Authenticated HTTP tests use real JWT validation with fixture data: both business
  owners are denied the other's exports and sync requests; wrong-tenant exports
  are rejected. These are not authenticated tests against production databases.
- CI installs test dependencies and includes profile recovery tests.
- Public health now includes the Render commit revision for deployment verification.

Verification: 34 production/authenticated tests and 14 POS recovery/sync tests pass.
The final frontend production rebuild passed.

Public deployment evidence: Vercel reported success for the prior 6bdd30b commit.
AdminCore health responded successfully but reports production_config_ok=false:
"Replace the development POS bridge key in both production services".
This requires matching production keys in the Render configuration of both services.
Local login credentials were found. Automatic approval review blocked using them
at the deployed login URL pending explicit user approval. No credentialed production
check has been performed in this follow-up yet.
