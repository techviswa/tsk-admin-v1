# Business billing mode controls

In AdminCore, select a business and open **Control Center → POS billing modes**.
Table, Token, Pickup and Delivery can be enabled independently. Saving all four
disabled prevents new bills; existing invoices, payments and history are retained.

The business policy is versioned in AdminCore. Stale saves return a conflict.
Changes use the existing durable entitlement worker and authenticated POS bridge.
The screen distinguishes defaults, saved/pending delivery, incomplete provisioning
and a policy confirmed by POS. Reload the saved policy/status to check confirmation;
failed delivery remains visible in POS Bridge and is retried through its existing controls.

POS stores the policy in its existing PostgreSQL-backed SaaS configuration.
The billing selector disables unavailable modes, preserves the current cart, and
requires the operator to select an enabled mode. The invoice and staff-order
services enforce policy independently of the browser. Active tills receive a
business-scoped invalidation event and refresh on focus/reconnect.

Existing installations retain all four modes until an explicit policy is saved.
Mode controls do not grant plan features, staff permissions or table-management
access. Offline tills use their last saved menu policy; a missing policy disables
checkout. The server checks the current policy when an offline receipt is submitted,
so a later restriction can require the existing offline recovery workflow.

This controls new POS billing modes. It does not delete tables, cancel existing
reservations/orders, remove POS modules, or alter historical financial records.
Physical till and hosted deployment acceptance remain separate release checks.

## Verification

- 269 AdminCore backend tests passed with isolated MongoDB acceptance enabled;
  16 focused policy/worker checks passed after the final queue-priority change.
- 40 AdminCore and 86 POS frontend tests passed; both production builds passed.
- Both isolated paired runs passed four policy combinations through real APIs,
  workers and PostgreSQL configuration, stale-save rejection, and invoice/order
  service enforcement. The first also ran the full POS release suite; the second
  verified final queue priority and request-alias enforcement.
- The actual local AdminCore browser loaded the four business controls with no
  API/runtime errors. No policy was saved against existing business records.

The batch evidence is `billing-mode-controls-verification-2026-10-31.json`.
