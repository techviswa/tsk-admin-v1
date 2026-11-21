# Credential update diagnosis — 23 October 2026

The deployed user list reports one unfinished profile update with status
`retrying`. Its last error is `POS_RATE_LIMITED`; the job is waiting for a
five-minute POS cooldown. The queued notification was not confirmation that
credentials had changed. A second save collided with that existing job.

Prepared locally:

- A synchronous submission guard prevents double-clicks from creating two saves.
- A user with a pending, running, retrying, or failed job cannot submit another
  edit. The form displays the reason and retry time; failed jobs have an explicit
  retry action.
- Queued updates use an informational notice. A success notice appears when
  polling observes the job transition to `synced`.
- The backend returns structured conflict status and retry time without exposing
  queued credentials.
- Workers wait out the shared POS cooldown before claiming jobs. Background
  imports yield while credential jobs are unfinished, giving profile writes the
  next request window. This is priority within this API process; it is not a
  distributed rate limiter across other replicas or services.

160 backend regressions, seven frontend tests, and the final production frontend
build pass. Database entitlement integration checks also passed against a temporary
local MongoDB database, which was removed afterward.

These changes have not been deployed or verified against a live credential save.
No live queued password was replaced, cancelled, or discarded.

## POS source inspection and cross-project verification

The POS source is `C:\Users\viswa\OneDrive\Desktop\viswa pos table error`
(GitHub `techviswa/pos_v1`). Its staff sync endpoint has no general rate limiter.
The live AdminCore points to `https://pos-v1-fwjm.onrender.com`, which differs
from the local AdminCore environment file. Checks against that local environment's
destination do not establish the health of the production bridge.

After the cooldown, the live profile job reached POS but got HTTP 500 from its
`PUT /api/admincore/staff/:userId` endpoint. The underlying live exception has not
been confirmed. Render access is needed to inspect the production logs; the
Render integration was found and suggested but is not confirmed connected.

A definite POS bug was reproduced locally: conflicting user emails escaped as
Prisma errors and became generic HTTP 500 responses. The POS now checks email
conflicts before applying credentials, normalizes edited emails, and maps unique
constraint races to HTTP 409. Public health reports its deployment revision.

AdminCore recognizes confirmed validation rejections before any target was
applied as `rejected`, permitting corrected details to be submitted. Timeouts,
unknown server failures, and partially applied jobs retain the recovery guard.

Verified using the actual AdminCore profile worker, a temporary POS HTTP server,
and temporary fixture accounts in the local PostgreSQL database:

- New credentials are persisted by POS and accepted by its login endpoint.
- The previous password is rejected and the previous authenticated session ends.
- A conflicting email is rejected before a queued password is committed.
- The test fixtures are removed afterward. No production accounts are changed.

The harness uses real temporary MongoDB records and PostgreSQL fixture accounts.
It also verifies recovery after an interruption following the AdminCore credential
commit, without repeating the POS update or incrementing the session version twice.
It does not verify live deployment configuration or production sync.

Run locally:

```powershell
node tests/run_pos_credential_check.mjs "C:\Users\viswa\OneDrive\Desktop\viswa pos table error"
```

POS syntax, Prisma validation, database conflict, user policy, bridge context,
security, permission revocation, change notification, and durable sync recovery
checks passed. Both projects' source changes remain undeployed.
