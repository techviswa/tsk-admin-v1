# Product quality repairs - 30 October 2026

This batch addresses the concrete usability and consistency gaps found after the
outlet correction. It does not certify every screen, hosted deployment, financial
policy or physical device. Earlier readiness language was too broad.

## Changes

- Replaced local AdminCore JSON text areas with structured order items, stock by
  outlet, saved history, reservations, kitchen items and preparation statuses.
  Existing extra fields are preserved. Invalid saved collections are not silently
  reset. Numeric drafts use browser validation rather than replacing empty input
  with a saved number. Commas in product names no longer split order items.
- Both operational page families now present named detail fields, record status,
  explicit currency where supplied, and expandable supplementary information.
  Missing currency is stated rather than assumed. POS-owned records remain
  read-only in AdminCore; native POS workflows remain available.
- Replaced the overview's stored-flag “POS synced” claim with a connection label
  and a reference to per-resource refresh status. Operational pages show confirmed
  refresh times. Outlet refresh acceptance is explicitly described as queued.
- Added API-backed 50-row paging to the main operations table. Filters and business
  changes reset paging. Customer history now exposes count/offset/has-more and can
  load past the former 100-order cap. Status counts are no longer sliced to four.
- Protected asynchronous detail loads and history continuation against business
  changes, other record selections and closing the dialog. Failed table loads
  have a persistent alert; missing snapshots do not show invented zero summaries.
- Monetary amounts show their supplied currency. Quantity/rate/count screens no
  longer sum unlike values into a generic financial-looking summary. Inventory
  quantities have bounded display precision without changing stored quantities.
- POS rankings expose additional rows and counts, source product rankings retain
  all computed rows, and a single sold item is labelled “1 unit”.
- The secondary POS dashboard now respects selected outlets, separates cached
  outlet views, uses outlet inventory balances, validates outlet ownership, and
  propagates database/schema failures. The sample-data fallback was removed from
  operational metrics. No routes or native business features were removed.
- Header controls and action rows can shrink/wrap at small widths. Browser checks
  verify desktop/mobile operational screens and outlet layouts down to 320px.
- Automatic outlet/product/operational reads now require a complete source
  business-and-tenant mapping before scheduling refresh work. Partial links stay
  POS-owned (local writes remain blocked), have an explicit connection-review
  state, and are not shown as operationally ready. Unsafe refresh/create controls
  are disabled until the connection is reviewed.
- The sync console rejects late live previews/results after a business change,
  clears old previews, and disables manual source actions on incomplete mappings.
  Technical diagnostics are expandable; its wording accounts for background sync.

## Verification

- AdminCore backend: 256 tests passed, including real isolated MongoDB projection
  checks and customer history paging beyond 100 records.
- AdminCore frontend: 32 tests in 11 suites passed. Tests cover paging, stale
  details/live previews, preserved item data, malformed collections, numeric
  drafts, currency labels and incomplete-connection action guards.
- POS frontend: 84 tests in 29 suites passed, including the expandable ranking.
- POS database regression passed for unavailable data, separate outlet caches,
  selected-outlet balances/totals, foreign references and staff outlet access.
- Final Chrome acceptance passed: eleven operational screens on desktop/mobile,
  a populated customer detail dialog, and outlet layouts at 1440/768/390/320px.
  No captured API/runtime errors or page-wide overflow. A screenshot was inspected.
- Both production builds passed. The paired run
  `coupled-2f6d9de9005444509fd7aaaf68edd861` passed, including the complete POS
  release suite, real API/worker restart delivery, credentials, privacy, frozen
  exports, multi-till recovery and restoration of all 63 PostgreSQL tables and
  MongoDB identity/index checks. The focused follow-up
  `coupled-0bd8bd2c1cda401f88e8d3800d96f396` also passed against the final backend
  readiness guard; it did not repeat the full POS suite. Local logs are ignored
  under recovery-backups/quality-*. The final Chrome run also verified the partial
  connection warning and blocked refresh; desktop/mobile screenshots were inspected.

The first new UI test runs exposed test-harness issues (router mock resolution
and selecting a status button instead of the record edit action). Both were
corrected; no production validation was weakened to obtain the passing result.

## Data and release boundaries

Business records were not reset or deleted. Tests create isolated fixture
businesses or disposable databases and clean up only their own fixtures. Existing
inactive outlets remain accessible through deliberate history/status views.
Secrets, real payment approval, device printing, financial policy acceptance,
hosted configuration/restore/rollback and exact-revision remote CI remain separate
release gates. No commit, push or deployment is part of this batch.

One inherited local business has an external ID from an older credential-check
fixture, no tenant mapping, and no corresponding POS source business. It is kept
visible for connection review. Its failed snapshot diagnostic is retained; global
operations health must not be called green while it remains unresolved. No source
ID was guessed and no record was deleted merely to obtain a healthy indicator.
Live checks confirmed original business/product/table IDs and unchanged counts:
3 businesses, 27 users, 13 outlets, 46 products, 9 bills and 5 tables. Reads of the
partial business did not create jobs or reset retry attempts; explicit sync was
denied. No entitled populated legacy CRM history was available for a live paging
check; the 201-record isolated unit case passed. The final frontend was reloaded
and verified at http://localhost:3001; the POS API runs on 4001, without starting
the POS frontend.

Safe evidence and current source fingerprints:
[product-quality-verification-2026-10-30.json](product-quality-verification-2026-10-30.json).
