# Role-consistent billing stock and checkout

The false waiter ingredient warnings came from denied inventory-management
requests being converted to empty arrays and then interpreted as zero stock.
Billing now has an authenticated read-only ingredient snapshot scoped to the
current business and permitted outlet. It shares the existing allocation rules:
an enabled outlet balance takes precedence; otherwise central stock applies.
Only ID, name, unit and effective quantity are returned. Management permissions
are unchanged and costs/vendors are excluded.

Failed stock reads are shown as unverified rather than a shortage. Dated offline
snapshots retain available ingredient data; checkout still validates stock on
the server. Missing ingredient IDs do not manufacture zero balances.

Waiters retain invoice creation and payment collection, as the owner explicitly
requested. Empty-cart checkout/payment selection is disabled. Discount, loyalty,
gift-card and tip controls remain available in compact expandable sections;
applied values remain visible. Search/categories stay visible when scrolling,
with an Order details return action. Duplicate ingredient badges were removed
and stock/category text contrast improved.

Verification: 102 frontend tests passed, the final focused Billing test passed
seven checks, and the production build passed. Disposable API run
`coupled-88cc99154e75403abc1c806bbdff7e1b` verified owner/waiter stock parity,
central fallback, real zero, field exclusion, outlet/management permission
boundaries, empty-bill rejection, waiter invoice creation and confirmed cash
collection. Browser geometry passed at 1915, 1440, 1024, 768, 390 and 320px with
actual component markup and compiled CSS; repeated DOM cards stressed scrolling
only and did not create products. Desktop/mobile screenshots were inspected.

No existing business data, financial rules or staff permissions were changed.
The local POS API was reloaded. Hosting and physical till/provider acceptance
remain separate release checks.
