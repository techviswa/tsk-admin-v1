# Local business recovery and consistency review

The earlier local-mode change selected `admincore_local`, leaving previous MongoDB
databases intact. The requested populated POS business was found in the legacy
`backend/prisma/dev.db` SQLite file, opened read-only during recovery.

The user confirmed recovery of `demo-business` (Demo POS Business). A PostgreSQL
backup was taken before an additive transaction copied its related records.
Existing records and passwords were preserved. Shared role, permission, product,
inventory and account identities were matched by their natural keys, with foreign
keys and embedded JSON references translated accordingly. A conflicting identity
aborts the transaction. No table was truncated and SQLite remains unchanged.

Verified PostgreSQL result: 46 products (including the newer product), five tables,
nine bills and 25 users. AdminCore imports confirmed 46 products, five tables,
nine bills, 39 inventory items and 25 staff records with zero import errors.
The recovered business is linked to `demo-tenant`, not a newly invented tenant.
Backups of PostgreSQL and local MongoDB are in the ignored `recovery-backups/`.

The restored owner's existing POS login was verified, followed by successful menu,
table, bill and KOT API reads. The recovered bill total exactly matches SQLite.
AdminCore's existing administrator login was preserved. Select Demo POS Business
after refreshing the local application to view the recovered records.

The review also corrected:

- Different Starter plan definitions: the bridge now sends explicit feature grants
  and limits, instead of assuming plan names have identical meanings.
- Feature changes: durable jobs reconcile desired grants every 30 seconds;
  confirmed failures keep their bounded retry state. POS Bridge provides an explicit
  feature-access retry. Explicit module switches affect the translated grants.
- Empty POS feature sets: explicitly disabling all features stays disabled instead
  of falling back to defaults.
- POS refresh hooks: a state update after a response no longer starts another
  request through a changing effect callback.
- Administrator bootstrap: restarting the backend preserves an existing account's
  chosen password. Bootstrap environment credentials create new accounts only.
- Imported businesses retain verified tenant IDs and existing commercial settings.
  Their outlets come from POS instead of creating a synthetic default on import.
- Unmapped change notifications cannot create businesses. This prevents delayed
  test events from introducing orphan business rows; the two confirmed empty test
  artifacts from previous checks were removed after the local MongoDB backup.

Validation: 170 backend tests, seven AdminCore frontend tests, the POS refresh-loop
regression test, real PostgreSQL feature-state checks, and the real MongoDB/POS
credential recovery check passed. Both frontend production builds passed. Expired
subscriptions retain historical feature visibility; lifecycle guards enforce
read-only or suspended access separately.

Local fixes and recovery do not deploy either repository or establish production
health. No production database was modified during this recovery.
