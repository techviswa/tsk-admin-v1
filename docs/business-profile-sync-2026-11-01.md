# Shared POS business profile

The shop information form previously saved to one unscoped browser localStorage
entry. It never updated the POS Business row, so exports and AdminCore retained
the old name. Different staff browsers could also use different shop details.

## Ownership and flow

- The POS Business row remains the name/tenant identity. A business-keyed
  PostgreSQL StateDocument stores GST, address, phone, receipt text, currency,
  default tax display, and offered payment-method preferences. No schema reset
  or new database provider is involved.
- Profile reads and exports use a consistent transaction snapshot. Profile
  writes update the Business revision, settings document, durable notification,
  and live change event in one transaction. Saves use compare-and-set; stale
  revisions are rejected rather than silently overwriting another editor.
- Signed-in POS staff read their own business profile. Settings permission is
  required to edit it. Bridge writes require the integration key and matching
  tenant identity. No plan, staff credential, permission, or lifecycle setting
  is accepted by the profile whitelist.
- POS header/sidebar display the business name separately from the staff name.
  Staff sessions refresh on business change notifications, reconnect/focus,
  and a periodic fallback. Browser snapshots are keyed by business and late
  responses from another account are ignored.
- AdminCore imports the profile and source revision, preserves platform-owned
  plan/status/type and branding color, and rejects older source snapshots.
  Connected-business renames are sent to POS first using the source revision.
  The local business row changes after POS acknowledgement; notification and
  reconciliation also recover interrupted reads/deliveries.

## Existing browser preferences

The old browser entry is retained. It is not automatically copied into a
business because that entry has no tenant identity. Settings offers explicit
review for businesses without a persisted profile. Operators must check the
business and values before saving. Existing business records, users, outlets,
sales and features are preserved.

Reset prepares a draft and retains the shop identity/contact fields. Failed
table-settings reads cannot enable saving unverified defaults. If the profile
saves but table preferences fail, the message states that partial outcome.

Personal account details are separate from the shop profile: the form displays
the signed-in user's real name/email. Account names save through the existing
staff self-profile API and update the session display after acknowledgement;
login email remains under staff administration permissions. Invalid account
names are rejected on the API. Settings cards, including the system-information
table, fit the mobile viewport. System information displays confirmed settings
and does not invent a last-backup timestamp.

Deploy the paired POS and AdminCore changes together. Hosted deployment,
payment-provider acceptance, physical devices and financial-rule acceptance
remain separate release checks; this repair does not claim all future sync
failures are impossible.

## Verification

- Isolated cross-project API round trip passed in run
  `aced03a0e6b142f08c1014c131927ae4`: authorized POS Settings write, waiter read
  and edit denial, durable profile/contact import, confirmed AdminCore rename,
  personal account-name delivery, invalid-name rejection, stale revision
  rejection, wrong tenant/key rejection, unchanged email/role/plan/status.
- POS frontend: 113 tests passed. AdminCore frontend: 44 tests passed.
- AdminCore backend: 261 tests and 28 subtests passed; eight existing tests skipped.
- Both production frontend builds passed with CI lint checks enabled.
- Existing record IDs were captured before restarting the local APIs; verification
  uses disposable databases for writes and read-only checks against the saved
  merchant data. No data reset, migration, publication or deployment was performed.
- Live desktop/mobile browser checks passed for header/sidebar names, Settings
  and Billing navigation, retained drafts, every Settings card's width, and
  AdminCore's synchronized business/contact panel without runtime/API errors.
  All original business/user/outlet/product/bill/table IDs remain present in both
  local databases. Earlier browser-only preferences are offered for review only
  when an actual legacy browser entry exists; defaults are not invented for migration.
