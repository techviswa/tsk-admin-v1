# Local development and production

`localhost` identifies the server serving a particular request. Previously only
the frontend ran locally: its `/api` requests were proxied to Render, and Render
called the deployed POS service. The retry notice therefore came from a live
credential job, not from a local POS update.

## Local mode

Run `npm run dev` in the AdminCore root. The launcher starts:

| Component | Local destination |
| --- | --- |
| AdminCore UI | http://localhost:3001 |
| AdminCore API | http://127.0.0.1:8000 |
| AdminCore database | Local MongoDB, `admincore_local` |

The ignored `.env.development.local` supplies stable local secrets and the local
administrator login. Its example is tracked. The launcher starts only AdminCore,
checks its readiness and stops its own children if a service fails. It does not
start or stop the separate POS project. Port 3000 may belong to that application;
AdminCore uses port 3001. The configured POS bridge remains an external service.
There is no automatic Render fallback.

AdminCore's `POS_CORE_API_KEY` and `ADMINCORE_API_KEY` in its ignored local
configuration must match the POS backend's `ADMINCORE_API_KEY`. The separate
launchers do not override each other's keys. Restart AdminCore after changing
its local configuration.

The frontend defaults to the local API even when started separately with
`npm run dev:frontend`. Its development banner shows the API environment.
Local data is separate from live data; nothing is automatically copied or deleted
on Render. A new local database starts with reference catalogs and an administrator,
without demo businesses or invented customer accounts.

Administrator environment credentials are used for initial account creation only.
Restarting either the local backend or a deployment does not reset an existing
administrator's password. Change existing passwords through the account workflow.

## Credential consistency

AdminCore durably records a pending request in MongoDB before synchronization.
The POS user update, outlet/permission changes, session revocation and notification
outbox commit in one PostgreSQL transaction. An outbox failure rolls all of those
changes back. AdminCore updates its profile and session version atomically only
after verified POS acknowledgement. Persisted checkpoints prevent replaying completed
POS targets after an interruption. Confirmed pre-write validation rejections can
be corrected; unknown failures and partially completed jobs retain recovery state.

This is recoverable synchronization between two databases, not a distributed
transaction that promises instantaneous consistency during outages.

## Release checks

1. Configure real deployment secrets and public HTTPS URLs using
   `.env.production.example`. Production refuses missing settings, placeholders,
   weak bridge secrets and localhost destinations. The bridge key must match the
   POS service's `ADMINCORE_API_KEY` exactly.
2. Back up the production databases, review pending POS migrations and apply them
   through the normal deployment process. Do not reset a production database.
3. Build the frontend with same-origin `/api` routing and deploy both source fixes.
4. Use `/api/ready` for AdminCore readiness and `/health/ready` for POS readiness.
   AdminCore startup requires valid production settings and MongoDB connectivity.
5. Verify both deployed revisions, authenticated tenant isolation, credential
   updates, session revocation and durable recovery against the deployed services.

Local verification includes `tests/run_pos_credential_check.mjs`, which uses the
actual AdminCore API, temporary MongoDB job records, the actual POS API and temporary
PostgreSQL fixture accounts. It checks password/login changes, old-session rejection,
duplicate-email rejection, interruption recovery and one committed outbox event.
Its unique test databases and fixture records are removed afterward.

These source changes do not deploy themselves. The earlier live Render job is
separate from this local environment; a live deployment and verification are still
required before claiming that production incident is resolved.

Product and outlet changes now use durable commands. HTTP 202 means queued;
confirmed values change after POS acknowledges the operation. Edit requests for
POS-linked records must include `expected_updated_at` from the loaded record's
`pos_metadata.updated_at`; delete requests send the same token as a query parameter.
HTTP 409 means the record needs refreshing or has an unfinished command. Retry an
uncertain failed command through its retry action rather than creating a new one.

Bridge exports advertising `export_paging: 3` use signed continuations. Start at
offset zero, then send both `meta.pagination.next_cursor` as `cursor` and
`next_offset` as `offset`, keeping the resource, filters and limit unchanged.
Continuations expire after 15 minutes; restart from the first page after expiry.
AdminCore follows these pages automatically. Deploy the updated AdminCore client
before enabling this POS protocol.

AdminCore now requests `consistent=true`. POS returns HTTP 202 with a durable
export identity while it builds a repeatable-read snapshot; AdminCore polls that
identity and follows its signed `frozen-snapshot-v1` continuation. Pending work
is never interpreted as an empty successful export. Snapshot pages expire after
15 minutes; restart an expired export. Privacy erasure invalidates retained
snapshots for that business. Native POS screen paging is unchanged.

Background snapshots support up to 1,000,000 rows, in pages of at most 250.
Legacy derived collectors have a 64 MiB memory budget; exceeding a safety limit
returns an explicit error. Narrow the export scope rather than accepting a
truncated result. A real 50,001-row check exercises the background path beyond
the former synchronous cap. Only complete, unfiltered exports starting at page
zero may infer absent records. Partial or legacy exports cannot infer removal.

Queued product, outlet and staff changes require the POS `actor_checks: 1`
capability. Current AdminCore authority is checked at delivery and tenant commands
also identify the initiating POS user. POS rejects new writes after that account
loses authority. Matching durable receipts remain confirmable after revocation,
without repeating credential writes or invalidating newer sessions. Deploy the
updated POS bridge before enabling these queued writes in the updated AdminCore.

When rotating the shared bridge key, retain old privacy digest keys in
`POS_PRIVACY_PREVIOUS_KEYS` on AdminCore for as long as their erasure markers are
retained. These values are secrets. Markers containing an unknown key identifier
remain unfinished rather than being reported as successfully scrubbed.

Use authenticated `/api/operations/health` with a platform audit permission to
monitor queue failures, stale work and the POS contract. HTTP 503 requires
investigation; the endpoint exposes counts rather than job payloads. Follow the
existing retry actions for failed commands. Do not clear pending flags or copy
confirmed fields directly in the database to make an alert disappear.

For local acceptance, run the backend tests with `RUN_PROJECTION_DB_TESTS=1`,
both frontend test/build commands, then:

```powershell
backend\.venv\Scripts\python.exe tests/coupled_api_check.py --pos-root "C:\Users\viswa\OneDrive\Desktop\viswa pos table error" --pos-suite --offline-browser
```

The optional browser check requires the POS production build and installed Chrome.
It runs headlessly against the runner's disposable PostgreSQL database. The runner
also verifies a PostgreSQL dump/restore across all tables and a BSON MongoDB restore
with indexes in unique fixture namespaces. Dumps and private diagnostics remain
under ignored `recovery-backups`; publish only the safe result artifacts.

Offline receipt capture refuses a full, unavailable or corrupt storage scope.
It preserves the cart and existing receipts, and exposes a recovery JSON download.
Completed receipts use minimal markers; older queue arrays are retained for
recovery. Device storage and downloaded recovery files need an operator retention
procedure. Never clear browser data while unsynchronized sales remain.

Offline menus are specific to the outlet and service channel. Load each required
channel online before relying on it offline; a cached dine-in price is not a
substitute for an uncached delivery price. Queued sales are priced by the server
on replay. The business must approve how differences from provisional offline
totals and cash collected are reviewed before enabling this workflow live.
