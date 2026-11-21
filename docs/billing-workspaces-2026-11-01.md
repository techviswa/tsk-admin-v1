# Billing workspaces and local service recovery

## Observed failures

AdminCore's API on port 8000 was not running while its frontend and POS were
still available. Direct health requests failed, the Admin development proxy
returned 504, and the POS subscription proxy could not reach AdminCore. This
was not evidence of a database aggregation timeout. The exact reason the old
independently launched API exited was not established.

The supported Admin startup is `npm run dev` from the AdminCore root. It starts
the API and UI together, waits for readiness, checks that port 3001 serves
AdminCore, and stops its own children when one exits. On Windows it now resolves
the configured virtual environment's actual interpreter and package directory
so the supervisor tracks the running API interpreter rather than its redirector.
It does not start the POS app or alter its database or integration keys.

Overview reads preserve confirmed values during refresh failures, label
unavailable values honestly, offer inline retry, discard responses from previous
business selections, and refresh visible screens without overlapping requests.
Business-name refreshes alone no longer repeatedly reload the overview.

## Final locations and behavior

| Capability | Location |
| --- | --- |
| Recent and older invoices, receipt details, payment actions | POS Bills & history (`/bills`) |
| Cash drawer, shift open/close, settlement search and history | POS Cash drawer (`/cash-drawer`) |
| Guest requests for an unpaid invoice | Compact Guest bill requests dialog in Billing |
| Owner/organisation commercial contact directory | AdminCore Owner contacts (existing `/clients` route) |

Owner contacts are separate from Users & Roles login accounts and Customers CRM
shopper records. Existing contact APIs, permissions and records remain in place.

The cashier drawer retains the existing Owner/Manager/Cashier role boundary.
Waiters retain permission-based billing, bill history and payment collection.
The drawer page explicitly loads its first report; it does not depend on a
browser's initial details-toggle event. Guest requests remain beside checkout
because issuing the requested invoice is a checkout task. Background inbox
errors or new requests do not open a dialog over an operator's current entry.

Bills & history uses opt-in server paging with outlet, author, search, payment,
date and amount filters applied before pagination. PostgreSQL pages directly,
with an ID tie-breaker for identical timestamps; it does not fetch every scoped
invoice ID into application memory. UTC timestamp comparisons preserve the
existing date-filter convention. The ordinary array API and frozen exports
retain their existing contract. Late responses cannot replace another outlet's
history, and failed reads offer retry without reporting a false empty history.

Linked receipt items can start a new cart using current menu prices, options
and stock checks. Batch quantities and shared ingredient demand are checked.
The original invoice, collections, customer and table reservation are not copied
into the new sale. Some existing restored invoices have null product references;
their history explains this and offers a new sale with manual product selection.
No product is inferred from a historical item's name and no custom SKU is invented.

The cart has a wider desktop column, wrapping descriptions, larger quantity
controls, collapsible optional adjustments and a visible desktop checkout total.
Empty carts hide optional adjustments. Mobile links jump directly between
products and the cart without scrolling through the entire product catalogue.

## Verification

- AdminCore frontend: 45 tests passed; CI production build passed.
- POS frontend: 131 tests across 37 suites passed; CI production build passed.
- Disposable API acceptance: run `970cb029b897485ba3f20d6053df472c` passed.
  Includes 105 scoped/searchable settlements, immutable closure/replay, 25 QR
  requests across pages, interleaved-outlet history paging, search/amount/payment/
  UTC date filters, invalid queries, forbidden outlets, unchanged array contract,
  waiter-owned history, waiter invoice creation and cash collection.
- Actual local browser checks: Admin overview and contact explanation, POS
  subscription proxy/screen, drawer auto-load/history, guest request dialog,
  cart quantity changes, desktop checkout bounds, mobile cart jump and legacy
  receipt handling. Widths: 1440, 1024 and 390 pixels. No API/runtime errors.
  No merchant invoices, shifts or profiles were saved by browser checks.
- Original IDs preserved in both databases. POS: 3 businesses, 26 users,
  15 outlets, 46 products, 9 bills, 5 tables. AdminCore: 3 businesses, 27 users,
  13 outlets, 46 products, 9 projected bills, 5 projected tables.

Local addresses: AdminCore http://localhost:3001 and POS http://localhost:3002;
their APIs use 8000 and 4001 respectively. Source changes are local and have not
been published or deployed. Hosted verification, production credentials,
payment-provider approval, printer/device testing and business acceptance of
financial rules remain separate release gates.
