# Deleted business assignments and POS synchronization

Checked 22 October 2026.

The deployed API contains users whose `business_ids` still reference deleted
businesses. Three accounts are affected; the reported user contains the four
IDs from the screenshot. The business delete route did not remove memberships.
No production records were repaired during diagnosis.

## Changes prepared locally

- Platform-admin user/client saves remove already stored references to deleted
  businesses. Newly submitted unknown IDs still fail validation. Other roles
  retain their existing access checks.
- Recovery refuses to discard a verified POS user link for a missing business.
  That mapping requires reconciliation instead of silent deletion.
- Profile jobs retain the full original membership snapshot for concurrency
  checks, but only contact surviving businesses. Removal from a surviving
  business still deactivates the corresponding POS account before local changes.
- The user Sync to POS action uses the same recovery path.
- Deleting an unlinked business removes user/client memberships and revokes
  affected sessions. Linked businesses and unfinished profile/provisioning jobs
  block local-only deletion; suspend a linked business instead.
- Refreshing the business list clears a deleted selection. User/client forms
  explain obsolete assignments.
- Individual POS Bridge syncs use durable snapshot jobs, like Sync All. The
  bridge displays the POS cooldown and queues work for automatic retry.

## Verification and limits

- 147 backend unit/regression tests passed, including recovery, authentication,
  permissions, tenant isolation, partial failure, and rate-limit retries.
- 5 frontend tests passed. The production frontend build passed.
- MongoDB entitlement integration tests were excluded: a running test database
  was unavailable. Mock-based checks do not replace database integration tests.
- Read-only live checks confirmed orphaned assignments. Scoped POS exports
  returned HTTP 429 `POS_RATE_LIMITED`, so live data consistency was not verified.
  See `sync-integrity-check-2026-10-22.json` for the partial diagnostic result.
- Backend changes are not deployed. Localhost proxies to the deployed Render
  API, so refreshing the browser cannot apply these backend fixes. Deployment
  and authenticated save/sync verification are still required.
- The connected POS project's source was not changed or audited in this task.
  This work does not establish that all workflows in both projects are error-free.
