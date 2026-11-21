# Settlement and QR billing panels

The POS Billing page now separates the drawer summary, opening/closing workflow,
and settlement history. Cash entry has a currency label, decimal validation and
aligned controls. Closing requires a review of counted cash and the displayed
variance before confirmation. The server remains responsible for the final
settlement calculation and immutable stored report; financial rules are unchanged.

History searches cashier names/shift IDs on the server and pages through every
matching settlement, scoped by business and outlet. The paged API is additive;
older consumers retain their array response. Closed records show operator,
timestamps, opening cash, collections/refunds, expected cash, counted cash and
variance. Missing historical values are identified rather than invented as zero.

Outlet-restricted staff remain scoped when they omit the outlet parameter. A
single assigned drawer can be inferred; staff with multiple assignments must
choose an outlet for current/open/close/report actions.

The QR queue distinguishes loading, confirmed empty, read failure and invoice
failure. It shows individual guest/table cards with order time, amount, loyalty
request and existing issue/split actions. Requests page on the server, and
already-billed split orders are excluded before paging. No payment is confirmed
by issuing an unpaid invoice. A rejected points request retains the existing
issue-without-points recovery action.

Request generations protect outlet changes, late reads/writes and split-invoice
callbacks. Refresh preserves typed cash. These panels stay in the product pane;
they do not add another scroll container or obscure the cart checkout.

Verification: the complete POS frontend suite passed 92 tests. Disposable-database
acceptance verified 105 searchable/outlet-scoped settlements, immutable closure
and replay, and 25 pending QR requests across three authenticated HTTP pages,
with 11 already-billed split orders ahead of them. Actual rendered components
with compiled production CSS passed geometry checks at 1440, 768, 390 and 320px;
desktop/mobile screenshots were visually inspected. Browser fixtures mock data;
they do not constitute physical till or payment-provider acceptance.

Final API run `coupled-dbef2fb3fab54e809f66f58f671ac772` also verified an
authenticated restricted cashier: history/current requests without an outlet
remain assigned-outlet scoped, and explicit other-outlet settlement/QR reads
return 403. The final production frontend build passed.

No existing business data, POS routes or features were removed. No database
migration or financial-policy change was made. Hosting/device acceptance and
the previously documented release gates remain separate.
