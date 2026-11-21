# Administration and financial-view repair

The screenshots exposed real defects beyond the earlier operational-page checks.
The fixes below are general rules, not exceptions for the Demo business.

## Corrected causes

- Feature Flags, Audit Logs, Settings, Integrations and the POS bridge now use
  platform permissions for platform administration, instead of requiring a tenant
  to enable its own administration module first. Business scope, write permissions,
  integration-secret restrictions and tenant entitlement checks remain enforced.
  Generic entitlement retries additionally require modules.write.
- Failed reads remain visible with retry actions and do not become “no records”.
  Configuration reads reject stale responses after scope changes. Client search
  and operational search treat punctuation literally rather than as Mongo regex.
- Audit entity filtering happens in MongoDB before paging and counting. Existing
  audit records now load, POS entities have readable labels, and change details
  expand instead of being permanently truncated.
- Notification preference toggles submit the new value. Failed writes restore
  the last confirmed display and expose reload. Explicit writes can create a
  missing known preference using a stable business/key identity; no preferences
  were seeded and no POS settings were changed automatically.
- POS invoice serialization now preserves authoritative columns over stale JSON
  metadata and exports the real invoice number when supplied. Legacy invoice
  metadata remains supported. Import preserves an explicit zero rather than
  falling through to another numeric field. Bills use official invoice numbers
  or clearly labelled source bill references, never customer names as invoice IDs.
- Monetary summaries show their currency, preserve real zero values and separate
  currencies. Empty payment views explain that invoice sales are not payment
  transactions. Source-owned rows expose View rather than disabled edit/delete
  controls; operational writes still belong to POS.
- Filters use named outlets and labelled date bounds. A selected business is not
  contradicted by a second business selector. Missing outlet contact/location
  information is labelled, and outlet lookup failures expose retry.
- The sales definition remains available in an expandable explanation rather
  than filling the KPI card with accounting text.

## Confirmed data facts

The local POS contains nine stored invoices: eight have zero totals, none has an
official invoice number, and the total is 578. Those records were preserved.
The imported payment view has no transactions; none was invented to match sales.
AdminCore has no commercial client-owner records. POS staff and commercial client
accounts are separate, so no client account or ownership relationship was guessed.

## Verification

- 265 backend tests passed, including platform/tenant access, audit filtering,
  explicit preference creation, zero-value import and literal searches.
- 39 frontend tests passed, including failed reads and new/failed toggle values.
- POS serializer regression passed. The full POS release suite and paired APIs
  passed in coupled-3da910da33514030a95df5b1415e0d3e. Final bridge permission changes
  passed the focused paired run coupled-8b38ff6894984295aeca1c0424b727c5. Both runs
  include credential replay, privacy, frozen exports, stock recovery and isolated
  restore proof. These runs tested uncommitted working-tree changes.
- Chrome checked 29 screens: eleven newer operational screens, seventeen other
  administration/financial screens, and Outlets. It checked populated audit rows,
  invoice identities, responsive layouts and incomplete-connection warnings.
- Original business/product/table IDs and counts remain unchanged: 3 businesses,
  27 users, 13 outlets, 46 products, 9 bills and 5 tables.

Final build/browser logs and safe source fingerprints are recorded in
[the verification JSON](console-repair-verification-2026-10-30.json).
The frozen final AdminCore build passed. POS frontend code was unchanged in this
correction; its preceding 84-test/build evidence remains separate. Private logs
and screenshots remain ignored locally.

## Remaining boundaries

One inherited credential-test business has no tenant/source counterpart and is
still flagged for connection review. Its failure is retained; global protected
operations health remains 503, while authenticated bridge health is good. No
mapping was guessed, no business data or POS feature was removed, and no commit,
push or deployment occurred. Hosted verification, physical devices, providers and
business financial acceptance remain release gates. Passing these checks does
not guarantee that every possible workflow is defect-free.
