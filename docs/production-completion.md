# Production completion work

This is the active implementation ledger for the two readiness reviews.

## Administration/financial-view correction - 30 October 2026

See [console repair details](console-repair-2026-10-30.md). The prior browser scope
missed configuration and audit endpoints. Their platform-versus-tenant gating,
false-empty states, audit paging/filtering, settings writes and invoice DTO
precedence are now corrected. The expanded read-only browser tour covers 29
screens. Backend 265 and frontend 39 tests passed; the full POS suite and paired
acceptance passed in 3da910da33514030a95df5b1415e0d3e, followed by the final backend
permission run 8b38ff6894984295aeca1c0424b727c5. No production deployment is claimed.

## Product quality follow-up - 30 October 2026

See [the product quality repair record](product-quality-2026-10-30.md) for the
forms, details, sync wording, completeness, financial presentation, dashboard
scope and responsive-layout changes. Tests passing is evidence for the stated
cases, not certification of every workflow or permission to skip release gates.
Final evidence: 256 backend tests, 32 AdminCore UI tests, 84 POS UI tests, both
builds, desktop/mobile browser acceptance, and paired runs
`2f6d9de9005444509fd7aaaf68edd861` (full POS release suite) and
`0bd8bd2c1cda401f88e8d3800d96f396` (final backend readiness guard) passed.
Original IDs/counts were retained. One inherited credential-test business has no
source counterpart and remains flagged for connection review; global protected
operations health deliberately remains 503. Authenticated bridge health is good.
No deployment occurred. See the quality record for scope and limitations.

## Outlet consistency correction - 30 October 2026

Screenshots exposed a gap in the prior local readiness claim: active-only
operating selectors were paired with an unfiltered dashboard outlet ranking.
The current smoke script is isolated, but ten older bridge-named inactive outlets
remain in the local source database. They are retained, not deleted by name.

- AdminCore defaults to active outlets with explicit inactive, archived and
  all-status views. API filtering and total count occur before pagination.
- Both POS dashboards share a report rule: omit empty inactive outlets, retain
  closed outlets with transaction history (including refunded transactions),
  and respect the selected outlet. Failed source reads propagate as errors.
- Creation no longer pre-fills another Main Outlet. No business records,
  features, or historical transactions were deleted.
- Verification: 9 outlet backend tests, 5 outlet UI tests, POS report-policy
  assertions and sales-chart checks passed. Twelve authenticated live API checks
  across three connected businesses verified status/count behavior. Authenticated
  operations health returned 200 after local API startup. The cropped-sidebar screenshot
  has not yet been reproduced as a layout defect; visual verification remains.
  The older 29 October source fingerprints predate this correction.
  The full AdminCore frontend suite subsequently passed: 24 tests in 8 suites.
  POS smoke checks also passed against real HTTP endpoints using an isolated
  fixture business: an inactive outlet with sales remains in the ranking, an
  empty inactive outlet is omitted, and selected-outlet totals remain scoped.
  The first run failed on a missing required customerName in the new test
  fixture; the corrected final run passed. Local logs remain in ignored
  recovery-backups/outlet-api-regression-final.log. AdminCore compiled and
  its title was verified at http://localhost:3001.

Existing POS features and business history must be preserved. A passing local
build does not certify hosted configuration, hardware, or provider acceptance.

| Workstream | Required outcome | State |
| --- | --- | --- |
| Source identity | Immutable business/tenant/source IDs; no name-based merges | Command IDs, explicit legacy mapping review and scoped import checks implemented |
| Administrative writes | No local-only success for POS-owned operations; durable recovery | Durable product/outlet/staff writes and delivery authority verified locally |
| Sync delivery | Browser-independent reconciliation, retry visibility, ordering/replay safety | Scheduler, renewable leases, per-target checkpoints, receipt replay and privacy priority implemented |
| Export completeness | Complete paged exports and reliable removal semantics | Frozen background snapshots, signed paging, absence reconciliation and explicit limits verified locally |
| Financial summaries | Complete scoped datasets and consistent decimal arithmetic | Decimal net invoices and currency separation; source-derived CRM double counting corrected; business acceptance outstanding |
| Permissions | Per-business/per-screen/outlet authorization in both APIs | Membership/management routes, source export permissions, queued actor revocation and last-owner protection implemented |
| Frontend scope | Ignore old requests and preserve/reset drafts appropriately | AdminCore 23 and POS 83 frontend tests and both production builds pass |
| Coverage | Explicit supported summaries, transaction histories and POS-owned workflows | 16 operational views, including five transaction views; populated API checks pass |
| Lifecycle | Provisioning, credentials, assignments, suspension and renewals recover safely | Durable provisioning/credentials and inactive membership reconciliation implemented; hosted lifecycle checks outstanding |
| Privacy | Erasure/retention reaches projections, exports, jobs and logs | Source erasure, projection barriers, snapshot invalidation/expiry and previous-key handling implemented; device/operator retention procedure required |
| Coupled CI | Run both APIs/workers/databases and assert actual delivery | Local coupled acceptance passed; workflow added, remote run outstanding |
| Recovery operations | Backup/restore tooling, monitoring, deployment/rollback checks | Isolated PostgreSQL all-table/BSON restore proof and protected queue health implemented; hosted restore/alert setup outstanding |
| Financial acceptance | Cross-system receipts, refunds, cash, tips, loyalty and gift-card checks | Local source release suite and populated transaction exports pass; business rule approval outstanding |
| Offline/multiple tills | Explicit conflicts and recoverable rejected offline sales | Storage, multi-till stock recovery and headless offline checkout verified; physical device acceptance outstanding |
| Hosted acceptance | Production roles, HTTPS, secrets, MFA and deployed revisions | Requires deployment access |
| Provider/hardware acceptance | Payments, messaging, printers and physical tills | Requires credentials/hardware |

Do not change a state to verified without recording the relevant test or live
check. Pending rows remain outstanding even when another workstream passes.

## Evidence, 28 October 2026

- All 208 AdminCore backend tests passed with `RUN_PROJECTION_DB_TESTS=1`.
  This includes real MongoDB totals over 1,501 records, mixed-currency separation,
  refund calculations and rejection of invalid/nonfinite invoice amounts.
- All 17 frontend tests passed, including late product responses after business
  changes and page-offset reset. The production frontend build passed.
- POS product-command tests passed against isolated fixture records: concurrent
  create replay, update replay, stale version rejection, atomic product/options/
  outbox rollback, numeric validation, concurrent stock adjustment and adjustment replay.
- Six POS operational export tests passed, covering scoped pagination, source
  identity, gift-card masking, anonymized profiles and restricted field selection.
- The complete POS `deploy-check.mjs` release suite passed against a newly
  migrated, disposable PostgreSQL database, followed by the paired API checks.
  Evidence: ignored `recovery-backups/coupled-c8219e45ff754d2e8d666affb90eff82/`.
  A subsequent focused regression also passed for a confirmed outlet assignment
  error: its update method used an undefined business variable. The fix preserves
  the outlet's product links and feature controls while updating staff assignments.
- `tests/coupled_api_check.py` creates a private PostgreSQL cluster and a unique
  MongoDB namespace, migrates fresh schema, starts both real APIs/workers, and
  verifies create/replay, restart with a demonstrably unconfirmed update, and a
  POS-only change reaching AdminCore without a browser. The populated payslip,
  tip payout, loyalty, gift-card transaction and marketing delivery views also
  passed through the real export/worker/API boundary, excluding private fields.
  Evidence: ignored `recovery-backups/coupled-61dc4a19c1594bb09b558c2233d1ab79/result.json`.
- The paired CI job runs this acceptance test and the full POS release suite in
  disposable databases. Configure `POS_INTEGRATION_REF` to the reviewed POS
  commit and `POS_REPO_TOKEN` if the paired repository is private. Local changes
  have not been published; the remote job has not yet verified them.

## Implementation details that affect operators

- Products retain confirmed values while a command is pending. A rejected change
  can be reviewed; an unknown delivery outcome remains recoverable. Legacy
  unresolved products require explicit source-ID review rather than name matching.
- Background reconciliation runs without an open browser and shares durable
  import leases with manual refresh and product writes.
- Complete snapshots hide absent source records while retaining their previous
  history. Partial exports cannot infer removals. Duplicate page identities or
  inconsistent offsets reject the snapshot instead of importing it.
- CSV downloads stream all matching rows in batches. PDF has an explicit 10,000
  record limit; larger downloads require a narrower scope or CSV. Sales downloads
  and summary cards both use invoices after refunds, including tax, excluding
  void/cancelled invoices. They are not a cash reconciliation.
- New financial/operational views are read-only POS projections. Payroll private
  profiles, gift-card redemption codes and marketing message bodies/provider
  errors do not cross these exports.

## Release gates still open

1. Publish reviewed commits from both repositories and run the paired CI workflow
   against those exact revisions. Local working-tree tests do not prove remote CI.
2. Supply production configuration and verify HTTPS, allowed origins, shared bridge
   credentials, least-privileged database roles, MFA and authenticated tenant isolation.
3. Exercise deployment/rollback, backup restoration and configured monitoring alerts
   on the actual hosted infrastructure. The local restore proof uses disposable data.
4. Obtain provider approval and test actual payments/messaging, printers, devices,
   network outages and operator recovery. Agree retention for old browser queues and
   downloaded recovery copies; do not clear unconfirmed receipts.
5. Approve the business definitions of sales/refunds, tax, shifts/cash, tips, loyalty,
   gift cards, stock and accounting dates using representative business scenarios.

This ledger deliberately remains open. Passing the checks above does not mean
all readiness work has been completed or that future failures are impossible.

## Current local verification, 29 October 2026

- All 252 AdminCore backend tests pass with isolated MongoDB acceptance enabled.
  All 23 AdminCore frontend and 83 POS frontend tests pass; both production builds
  compile successfully. Logs: `readiness-backend-final.log`, `readiness-admin-ui.log`,
  `readiness-pos-ui.log`, `readiness-admin-build.log`, `readiness-pos-build.log`.
- Frozen background exports support the documented safety limits and pass a real
  50,001-row / 201-page fixture, scoped continuation checks, concurrent price/edit/
  deletion checks, retention and privacy invalidation. Staff exports are read-only,
  preserving native screen initialization without invoking it inside snapshots.
- New user provisioning stays blocked from login until POS confirms it. Exact
  per-target commands survive lost acknowledgements; create/update receipt replay
  preserves newer sessions and creates no duplicate account or outbox notification.
  Fresh email collisions cannot overwrite another POS identity. Credential-only
  changes retain source role, custom permissions, outlets and profile requirements.
- Delivery checks revalidate current initiating authority. Last-owner changes are
  serialized in both services. An explicitly authorized retry can adopt a partial
  credential job after checking every intended business; the original command,
  receipts and initiating audit identity remain. Missing source staff memberships
  become inactive without deleting identity/history or other business memberships.
- Current and legacy delivery address/instruction fields participate in privacy
  erasure. Prior bridge keys support retained erasure markers. New stale projections
  cannot restore scrubbed contacts, and retained export chunks expire or invalidate.
- The corrected headless browser check passes offline reload, capture, saved stock
  holds, another reload and exactly-once server replay. It uses the selected
  channel's cached menu and current fulfillment validation. Storage failures and
  full/corrupt queues preserve existing receipts and expose a recovery copy.
- Paired API/credential acceptance passed in
  `recovery-backups/coupled-449919fb49e348e6b0c9805ea4825a2c/result.json`.
  PostgreSQL restoration verified all 63 tables and row hashes; MongoDB restoration
  preserved BSON types, identities and a unique index. This proves disposable local
  restoration, not a hosted backup policy or production recovery time objective.
- Local AdminCore was reloaded on port 3001 (API 8000), then only the POS API on
  4001. Its frontend was not started. Authenticated operation health is HTTP 200;
  queues have no failed or stale work. Older import failure history remains marked
  reconciled after a verified complete source snapshot, retaining its error and
  attempts. Credential/product/outlet command failures are never cleared this way.
- Original business/product/table IDs remain. Before/after counts are unchanged:
  3 businesses, 27 users, 13 outlets, 46 products, 9 bills and 5 tables. Checkpoints:
  `recovery-backups/readiness-local-before.json` and `readiness-local-after.json`.

The final combined POS release/browser/paired run passed in
`recovery-backups/coupled-5eba4e97321142da910261f2c0202868/result.json`.
It includes the full POS release suite, corrected offline browser fixture,
credential creation/update acknowledgement loss, multi-till recovery, frozen
exports, both database restoration proofs and real API/worker restart delivery.
Failed earlier attempts remain failed in their own result files.

The subsequent live-data check exposed a populated legacy kitchen row without a
tenant ID. POS bridge exports now attach the tenant from their verified business
query, reject mismatches and preserve normalized rows; AdminCore's strict scope
checks remain unchanged. The expanded fixture verifies populated kitchen delivery
and privacy cleanup by order reference. It passed in
`recovery-backups/coupled-a2cb3a0a919a4187bbdd8ac59cb16e01/result.json`.
The local POS API was reloaded again, and the original business's populated
kitchen refresh is confirmed synced. Original IDs and counts remain unchanged.

The safe final evidence and selected working-tree fingerprints are in
`docs/production-verification-2026-10-29.json`. Both repositories still contain
unpublished working-tree changes; their existing HEAD commits alone do not
identify this tested code. No production deployment is claimed.

## Membership and privacy development, 29 October 2026

- Settings, integrations, feature flags, business edits and audit reads now check
  the selected business membership. Global owner authority cannot grant access in
  a second business where the account is staff. Disabled memberships and suspended
  businesses cannot enter aggregate POS read scopes. Integration secrets remain
  owner-only in that membership. POS exports also require the corresponding screen
  permission rather than a manager role alone.
- Control Center counts use authorized resource filters. Dashboard totals now use
  detail-screen permissions and net invoice arithmetic, retain cents and currency
  separation, and show unavailable values instead of invented zeroes. Previous
  business values are cleared while a new scope is loading.
- Customer erasure commits keyed contact markers and receipt-reference markers
  with its outbox notification. Receipt/order/reservation/session/feedback contacts
  are cleared while financial amounts remain. Queued marketing is stopped and
  stored recipient/message data is cleared. Markers contain no literal contact.
- AdminCore persists an erasure barrier before cleaning retained projections.
  Imports and erasure share a business privacy lease, preventing stale imports
  from restoring erased details. Legacy CRM identifiers containing contacts are
  pseudonymized. Structured log references and embedded contact tokens are scrubbed.
- Latest full checks: 235 backend tests and 23 frontend tests passed. POS erasure
  acceptance passed for rollback when the outbox fails, cross-business isolation,
  retained invoice amounts and real scoped exports. Paired API erasure delivery
  passed in `recovery-backups/coupled-0dd87789f2e34cb8bc4f186de11816ce/`.
  Receipt-reference paging and subsequent changes require their final regression
  run. This is development evidence, not hosted acceptance.

## Outlet recovery completion, 28 October 2026

- Outlet creates, edits, status changes and deactivation use the same durable
  command processor as products. Confirmed fields remain unchanged until POS
  acknowledgement. Uncertain failures remain pending and have an explicit retry.
- POS creates use stable outlet IDs and command receipts. Outlet creation, menu/
  inventory links, staff assignments, feature controls and the notification outbox
  commit together. Parent Central Kitchen transactions now retain those guarantees.
  Deactivation preserves historical records. No schema reset or migration is needed.
- Product/outlet edit forms retain the source version from when the form opened.
  Updates/deletes require that version and reject stale requests with HTTP 409.
  The token is `pos_metadata.updated_at`, sent as `expected_updated_at` in the update
  body or delete query string. Integrations calling these AdminCore APIs must send it.
- Concurrent first requests initialize module defaults atomically. Concurrent
  create retries share one local MongoDB identity and one POS identity. Replayed
  audit writes also share one database identity.
- Older source mappings require explicit ID review. Active commands cannot be
  remapped, duplicate archival requires confirmation, and subsequent imports update
  the canonical record without reviving an archived duplicate. Rejected creates
  can be corrected or discarded without creating a second source record.
- All 220 backend tests passed with isolated MongoDB checks enabled; all 21
  frontend tests and the frontend production build passed. POS outlet-command
  tests passed for concurrent creates, stale edits, assignment/outbox rollback,
  parent transaction rollback, retained menus/features and historical deactivation.
- The full POS release suite passed in
  `recovery-backups/coupled-bda6566239494c82bc137248af59b08a/setup.log`.
  Final paired API acceptance passed separately in
  `recovery-backups/coupled-f26199362d7e4bfbb6fa05785ec58424/result.json`.
  The latter explicitly stops the old API before restarting its worker, including
  on Windows, and verifies still-pending product and outlet commands complete.
- Both local backends were reloaded. AdminCore is verified on port 3001, its API
  on 8000, and the connected POS API on 4001. A POS frontend was not started.
  Before/after counts were unchanged: 3 businesses, 27 users, 13 outlets,
  46 products, 9 bills and 5 tables. Original POS product/table IDs remain present.
  Checkpoints are retained under `recovery-backups/outlet-command-before/`.

These checks supersede the earlier, weaker Windows restart checks. They verify
the outlet work locally; the remaining readiness work above stays open.

## Deletion-safe export paging, 28 October 2026

- Bridge paging uses signed continuations tied to business, tenant, resource,
  filters and page size. Bounded primary-ID reads prevent earlier deletions from
  skipping later records. Grouped exports keep separate model positions; progress
  follows raw rows even when the projection filters every visible row.
- AdminCore follows the continuation and rejects missing tokens, changing scope,
  repeated identities and changing paging strategies before importing a partial
  result. Only complete unfiltered exports starting at zero can reconcile absence.
- Native POS screen paging is unchanged. No migration or data reset is required.
  Values are read per page, rather than frozen across an entire financial export.
  New concurrent records may require the next reconciliation. Background exports
  beyond the synchronous cap and privacy retention remain open work.
- All 226 Admin backend tests passed, including real MongoDB checks. Five cursor
  tests, six operational export tests and real PostgreSQL cursor checks passed.
  The full POS release suite and paired API restart/reconciliation checks passed
  in `recovery-backups/coupled-357cff9f05444740ae35d9d38501d0c9/`.
- Additional paired HTTP acceptance passed in
  `recovery-backups/coupled-a86120a655864aeea21ce5b953d79e6a/`, including signed
  one-record pages returning both fixture products exactly once and rejection of
  an unsigned continuation with HTTP 409.
- The compatible AdminCore client was loaded before restarting the local POS API.
  Source changes are reviewable in `docs/production-export-cursors.patch`.
- After reload, AdminCore compiled successfully on port 3001 and both APIs passed
  health checks. Original business/product/table IDs remain present. Counts remain
  3 businesses, 27 users, 13 outlets, 46 products, 9 bills and 5 tables; checkpoint:
  `recovery-backups/export-cursor-local-state.json`.
