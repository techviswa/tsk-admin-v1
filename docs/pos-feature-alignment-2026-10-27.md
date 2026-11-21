# POS / AdminCore feature alignment — 27 October 2026

POS source: `C:\Users\viswa\OneDrive\Desktop\viswa pos table error`.
AdminCore UI: `http://localhost:3001`; POS API: `http://127.0.0.1:4001`.

The POS navigation, route registry, Prisma models, sync contract, QR services,
and operational modules were compared with AdminCore. POS remains the source
of truth. The newer AdminCore views display scoped snapshots; operational writes
continue through the existing POS workflows, rather than changing a Mongo copy
that POS would overwrite on its next sync.

| AdminCore view | POS export | Data shown |
| --- | --- | --- |
| Attendance | `attendance` | Staff, clock times, breaks, source, notes; deleted shifts excluded |
| Tips | `tips` | Recorded tips, recipient, method, pooled state, void state |
| Payroll | `payroll` | Run periods, state, finalization, payslip count; private pay profiles excluded |
| Customer Profiles & Loyalty | `loyalty-customers` | Persisted profiles, visits, spend, points, consent; erased PII excluded |
| Gift Cards | `gift-cards` | Masked card reference, recipient, value, balance, expiry, state |
| Marketing | `marketing` | Campaign channel/type, schedule and actual stored state; provider secrets excluded |
| Menu Pricing / Happy Hours | `menu-pricing` | Discount rules, time zone/windows, product/outlet/channel scope |
| Guest Feedback | `guest-feedback` | Guest, rating, comment, bill reference and received date; feedback token excluded |
| Table Assistance | `table-assistance` | Requests, table, assignment and timestamps; ended-session requests shown expired |
| QR Ordering | Existing `qr` | QR codes, sessions and scans; now has its own AdminCore navigation/view |
| Central Kitchen | Existing `central-kitchen` | Purchase orders, allocations, routes and movements; now has its own navigation/view |

Product imports also retain the complete POS product projection in `pos_metadata`.
The existing product editor displays synchronized menu choices, combos, recipes,
channel settings, dietary information and outlet overrides. Its basic editing
workflow is retained; advanced menu changes belong to POS.

## Synchronization behavior

- New exports use the existing authenticated POS bridge and verified business /
  tenant context. Queries also explicitly constrain the owning business.
- Export paging has stable ordering; AdminCore consumes every page before import.
  The views page through imported records, 50 at a time.
- The active view checks its snapshot every 30 seconds, or every three seconds
  while refresh is pending. A successful snapshot is reused for 60 seconds.
  **Refresh from POS** explicitly queues a refresh. This is eventual consistency,
  not an atomic transaction spanning the two databases.
- Queued/retrying/failed states are displayed separately from the cached data.
  Polling does not reset an exhausted retry budget; explicit refresh can retry.
- Source creation dates and zero values are retained. Requests from a previous
  business cannot populate the newly selected business's view.
- After a complete, successful export of a new module, cached records absent
  from POS are hidden from its current view. Their last snapshot remains stored
  for audit. Partial, failed or unpaged exports never infer record removal.
- New operational projections reject local create/update/delete requests.
  Platform staff may inspect these read-only snapshots without enabling a tenant
  feature. Tenant readers still require the business's module entitlement.

## POS preservation and release

The user approved additive read-only exports. The existing POS sync service gained
an export branch and resource registration; its deployment check gained the new
tests. A separate `operational-exports.js` contains field-selected read projections.
No POS frontend, domain service, Prisma model, migration or environment setting
was changed. No operational records were fabricated, reset or deleted.

`pos-readonly-exports.patch` records the POS integration changes relative to the
pre-task working tree, preserving the user's earlier uncommitted development.
It is a review artifact; the changes have already been applied locally.

For a hosted release, deploy the POS export additions and AdminCore changes
together, keeping the bridge keys and verified business mappings aligned.
No Render/Vercel deployment was performed by this task.

## Verification

- New POS export tests cover paging, tenant ownership, erased PII, masked cards,
  excluded shifts/private profiles, outlet filtering, and expired assistance.
- Existing POS AdminCore reliability checks passed.
- Live POS-to-AdminCore imports and scoped list reads passed. At verification,
  Demo POS Business had two profiles, 53 QR records, 44 Central Kitchen records,
  and 46 products. The other new operational datasets were empty.
- A wrong-tenant payroll export request returned 403.
- All 11 views were opened in hidden Chrome, checking API/runtime failures and
  successful data loading. Screenshot: ignored
  `test_reports/admincore-pos-operations.png`.
- AdminCore unit regressions and production frontend build were run. Test logs
  are ignored; the final task response reports their results.

Optional local browser check: `node tests/pos_operations_browser_check.mjs`.
It reads ignored local credentials and uses a separate temporary browser profile.
