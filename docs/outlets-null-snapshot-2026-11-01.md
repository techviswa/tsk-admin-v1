# Outlets initial-selection crash

With no business selected, both `snapshot?.businessId` and `businessId` were
undefined. Their equality passed, so the next expression dereferenced
`snapshot.offset` while the snapshot was still null. The total-count expression
had the same issue.

Outlets now requires both a selected business ID and an existing matching
snapshot before reading its rows, offset or total. No-selection state renders
the business-selection prompt. Business/page scope and late-response protection
remain in place; no records or configuration were changed.

The full frontend suite passed 44 tests, including new null/undefined initial
selection, business selection/clearing and late-response cases. A fresh local
AdminCore browser verified direct Outlets navigation without a selection,
loading Main Outlet after selection, and returning to All businesses with no
runtime/API errors. Related snapshot comparisons in Products, POS Admin, POS
Operations and POS Bridge were reviewed; no equivalent null dereference was found.

The final AdminCore production frontend build passed. Existing business data and
backend configuration were unchanged.
