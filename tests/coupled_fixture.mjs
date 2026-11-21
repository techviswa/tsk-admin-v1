// Only used by coupled_api_check.py against its newly created disposable database.
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
assert(new URL(process.env.DATABASE_URL).pathname.startsWith('/coupled_'));
const { default: prisma } = await import(pathToFileURL(resolve(process.argv[2], 'backend/src/database/prisma/client.js')));
const businessId = 'coupled-business';
const date = new Date('2026-10-01T00:00:00Z');
try {
  if (process.argv[3] === 'billing-panels') {
    const load = relative => import(pathToFileURL(resolve(process.argv[2], relative)));
    const { settlementService } = await load('backend/src/core/billing/settlement.service.js');
    const outlet = await prisma.outlet.create({ data: { businessId, name: 'Panel fixture', code: 'PANELS' } });
    const other = await prisma.outlet.create({ data: { businessId, name: 'Other panel fixture', code: 'OTHER-PANELS' } });
    await prisma.stateDocument.createMany({ data: Array.from({ length: 106 }, (_, i) => ({ key: `shift-history:panel-${i}`, data: {
      id: `panel-${i}`, business_id: businessId, outlet_id: i === 105 ? other.id : outlet.id, status: 'closed',
      opened_by_name: 'Alice Cashier', closed_by_name: 'Alice Cashier', opening_cash: 100, closing_cash: 95, expected_cash: 100, variance: -5,
    } })) });
    const history = await settlementService.history({ businessId, outletId: outlet.id, query: { page: 11, limit: 10, search: 'alice' } });
    assert.equal(history.items.length, 5); assert.equal(history.pagination.total, 105); assert.equal(history.pagination.has_more, false);
    assert.ok(history.items.every(row => row.outlet_id === outlet.id));
    const shift = await settlementService.open({ businessId, outletId: outlet.id, openingCash: 100, user: { id: 'cashier', name: 'Alice', role: 'Owner' } });
    const closed = await settlementService.close({ businessId, outletId: outlet.id, shiftId: shift.id, closingCash: 95, user: { id: 'cashier', name: 'Alice', role: 'Owner' } });
    assert.equal(closed.variance, -5);
    assert.deepEqual(await settlementService.close({ businessId, outletId: outlet.id, shiftId: shift.id, closingCash: 95, user: { id: 'cashier', role: 'Owner' } }), closed);
    await assert.rejects(settlementService.close({ businessId, outletId: outlet.id, shiftId: shift.id, closingCash: 96, user: { id: 'cashier', role: 'Owner' } }), error => error.statusCode === 409);
    await prisma.order.createMany({ data: Array.from({ length: 25 }, (_, i) => ({ id: `panel-qr-${i}`, businessId, outletId: outlet.id, channel: 'qr', status: 'accepted', customerName: 'Panel guest', total: 10,
      metadata: { approval_status: 'approved', bill_requested: true, table_name: 'Fixture table' } })) });
    await prisma.order.createMany({ data: Array.from({ length: 11 }, (_, i) => ({ id: `already-billed-panel-${i}`, businessId, outletId: outlet.id, channel: 'qr', status: 'accepted', customerName: 'Already billed guest', total: 10,
      createdAt: new Date('2025-01-01'), metadata: { approval_status: 'approved', bill_requested: true, qr_bill_ids: ['existing-split-invoice'] } })) });
    const { usersService } = await load('backend/src/core/users/users.service.js');
    await usersService.createUser({ businessId, tenantId: 'coupled-tenant', payload: { name: 'Panel owner', email: 'panels@example.test', password: 'panel-fixture-password-123', role: 'Owner' } });
    const { default: app } = await load('backend/src/app.js');
    const { connectDatabase } = await load('backend/src/config/db.js');
    await connectDatabase();
    const server = await new Promise(resolve => { const listener = app.listen(0, '127.0.0.1', () => resolve(listener)); });
    try {
      const origin = `http://127.0.0.1:${server.address().port}`;
      const login = await fetch(origin + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: 'panels@example.test', password: 'panel-fixture-password-123', business_id: businessId }) });
      assert.equal(login.status, 200);
      const headers = { Cookie: login.headers.getSetCookie().map(row => row.split(';')[0]).join('; ') };
      const ids = new Set();
      for (let page = 1; page <= 3; page++) {
        const response = await fetch(`${origin}/api/public/qr/checkout/inbox?outlet_id=${outlet.id}&page=${page}`, { headers });
        assert.equal(response.status, 200);
        const payload = (await response.json()).data;
        assert.equal(payload.items.length, page === 3 ? 5 : 10); assert.equal(payload.has_more, page < 3);
        for (const row of payload.items) { assert.ok(row.created_at); assert.ok(!ids.has(row.id)); ids.add(row.id); }
      }
      assert.equal(ids.size, 25);
      await usersService.createUser({ businessId, tenantId: 'coupled-tenant', payload: { name: 'Scoped cashier', email: 'scoped-panels@example.test', password: 'panel-fixture-password-123', role: 'Cashier', profile_required: false, assigned_outlet_ids: [outlet.id] } });
      const scopedLogin = await fetch(origin + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: 'scoped-panels@example.test', password: 'panel-fixture-password-123', business_id: businessId }) });
      assert.equal(scopedLogin.status, 200);
      const scopedHeaders = { Cookie: scopedLogin.headers.getSetCookie().map(row => row.split(';')[0]).join('; ') };
      // Newer bills from another outlet must not consume this outlet's page.
      const historyIds = Array.from({ length: 90 }, (_, i) => `workspace-history-${i}`);
      await prisma.bill.createMany({ data: historyIds.map((id, i) => ({ id, businessId,
        customerName: 'History fixture', currency: 'INR', status: 'issued', total: i + 10,
        invoiceNumber: `WORKSPACE-${i}`, createdAt: new Date(i < 30 ? '2026-10-10' : '2026-10-11'),
        metadata: { outlet_id: i < 30 ? outlet.id : other.id, created_by: 'history-author', payment_type: 'Cash' },
      })) });
      try {
        const historyPage = async (query, actorHeaders = scopedHeaders) => {
          const response = await fetch(`${origin}/api/billing?paged=true&outlet_id=${outlet.id}&${query}`, { headers: actorHeaders });
          assert.equal(response.status, 200); return (await response.json()).data;
        };
        const first = await historyPage('page=1'), second = await historyPage('page=2');
        assert.equal(first.items.length, 25); assert.equal(first.has_more, true);
        assert.equal(second.items.length, 5); assert.equal(second.has_more, false);
        assert.equal(new Set([...first.items, ...second.items].map(row => row.id)).size, 30);
        assert.ok([...first.items, ...second.items].every(row => row.outlet_id === outlet.id));
        assert.equal((await historyPage('search=WORKSPACE-29')).items[0].id, 'workspace-history-29');
        assert.equal((await historyPage('min=39&max=39')).items.length, 1, 'amount filter');
        assert.equal((await historyPage('payment=Cash')).items.length, 25, 'payment filter');
        assert.equal((await historyPage('date=2026-10-10')).items.length, 25, 'date filter');
        assert.equal((await historyPage('min=39&max=39&payment=Cash&date=2026-10-10')).items.length, 1);
        assert.equal((await historyPage('min=39&max=39&date=2026-10-11')).items.length, 0);
        for (const query of ['page=0', 'page=1.5', 'min=-1']) {
          assert.equal((await fetch(`${origin}/api/billing?paged=true&outlet_id=${outlet.id}&${query}`, { headers: scopedHeaders })).status, 400);
        }
        assert.equal((await fetch(`${origin}/api/billing?paged=true&outlet_id=${other.id}`, { headers: scopedHeaders })).status, 403);
        const original = await fetch(`${origin}/api/billing?outlet_id=${outlet.id}`, { headers: scopedHeaders });
        assert.equal(original.status, 200); assert.ok(Array.isArray((await original.json()).data));
      } finally {
        await prisma.bill.deleteMany({ where: { businessId, id: { in: historyIds } } });
      }
      const scopedHistory = await fetch(origin + '/api/billing/shifts/history?page=1&limit=10', { headers: scopedHeaders });
      assert.equal(scopedHistory.status, 200);
      const scopedData = (await scopedHistory.json()).data;
      assert.equal(scopedData.pagination.total, 106);
      assert.ok(scopedData.items.every(row => row.outlet_id === outlet.id));
      const inferredShift = await fetch(origin + '/api/billing/shifts/current', { headers: scopedHeaders });
      assert.equal(inferredShift.status, 200); assert.equal((await inferredShift.json()).data.id, shift.id);
      for (const route of ['/api/billing/shifts/current', '/api/billing/shifts/history', '/api/public/qr/checkout/inbox']) {
        const denied = await fetch(`${origin}${route}?outlet_id=${other.id}`, { headers: scopedHeaders });
        assert.equal(denied.status, 403);
      }
      const ingredient = await prisma.inventoryItem.create({ data: { businessId, name: 'Shared stock fixture', unit: 'kg', stock: 20, conversionCost: 7.77, vendor: 'DO_NOT_EXPORT_VENDOR' } });
      const central = await prisma.inventoryItem.create({ data: { businessId, name: 'Central fallback fixture', unit: 'kg', stock: 9 } });
      await prisma.outletInventory.create({ data: { outletId: outlet.id, inventoryItemId: ingredient.id, stock: 4, enabled: true } });
      await usersService.createUser({ businessId, tenantId: 'coupled-tenant', payload: { name: 'Stock waiter', email: 'stock-waiter@example.test', password: 'panel-fixture-password-123', role: 'Waiter', profile_required: false, assigned_outlet_ids: [outlet.id] } });
      const waiterLogin = await fetch(origin + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: 'stock-waiter@example.test', password: 'panel-fixture-password-123', business_id: businessId }) });
      assert.equal(waiterLogin.status, 200);
      const waiterHeaders = { Cookie: waiterLogin.headers.getSetCookie().map(row => row.split(';')[0]).join('; ') };
      const stockPath = `${origin}/api/billing/ingredient-stock?outlet_id=${outlet.id}`;
      const stock = async actorHeaders => { const response = await fetch(stockPath, { headers: actorHeaders }); assert.equal(response.status, 200); return (await response.json()).data; };
      const ownerStock = await stock(headers), waiterStock = await stock(waiterHeaders);
      assert.deepEqual(waiterStock, ownerStock);
      assert.equal(waiterStock.find(row => row.id === ingredient.id).current_stock, 4);
      assert.equal(waiterStock.find(row => row.id === central.id).current_stock, 9);
      assert.ok(waiterStock.every(row => Object.keys(row).sort().join(',') === 'current_stock,id,name,unit'));
      assert.equal((await fetch(origin + '/api/inventory', { headers: waiterHeaders })).status, 403);
      assert.equal((await fetch(`${origin}/api/billing/ingredient-stock?outlet_id=${other.id}`, { headers: waiterHeaders })).status, 403);
      await prisma.outletInventory.update({ where: { outletId_inventoryItemId: { outletId: outlet.id, inventoryItemId: ingredient.id } }, data: { stock: 0 } });
      assert.equal((await stock(waiterHeaders)).find(row => row.id === ingredient.id).current_stock, 0);
      const writeHeaders = { ...waiterHeaders, 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
      const beforeBills = await prisma.bill.count({ where: { businessId } });
      const emptyBill = await fetch(origin + '/api/billing', { method: 'POST', headers: writeHeaders, body: JSON.stringify({ outlet_id: outlet.id, items: [] }) });
      assert.equal(emptyBill.status, 400); assert.equal(await prisma.bill.count({ where: { businessId } }), beforeBills);
      const saleProduct = await prisma.product.create({ data: { businessId, name: 'Waiter checkout fixture', category: 'Meals', price: 10, stock: 2 } });
      const checkout = await fetch(origin + '/api/billing', { method: 'POST', headers: writeHeaders, body: JSON.stringify({ outlet_id: outlet.id, items: [{ id: saleProduct.id, quantity: 1 }], payment_type: 'Due', payments: [] }) });
      assert.equal(checkout.status, 201);
      const invoice = (await checkout.json()).data;
      const waiterHistory = await fetch(`${origin}/api/billing?paged=true&outlet_id=${outlet.id}`, { headers: waiterHeaders });
      assert.equal(waiterHistory.status, 200);
      const waiterBills = (await waiterHistory.json()).data.items;
      assert.ok(waiterBills.some(row => row.id === invoice.id));
      assert.ok(waiterBills.every(row => row.created_by === invoice.created_by));
      const collected = await fetch(`${origin}/api/billing/${invoice.id}/payments`, { method: 'POST', headers: writeHeaders, body: JSON.stringify({ method: 'Cash', amount: invoice.total, client_request_id: 'waiter-payment-fixture' }) });
      assert.equal(collected.status, 200);
      const paidBill = await prisma.bill.findUniqueOrThrow({ where: { id: invoice.id } });
      assert.equal(paidBill.metadata.payments.filter(row => row.method === 'Cash' && row.status === 'confirmed').reduce((sum, row) => sum + Number(row.amount), 0), Number(paidBill.total));
      console.log('Waiter invoice creation and cash collection retained; empty invoices rejected without records.');
      console.log('Owner/waiter billing-stock parity, central fallback, true zero, private-field exclusion and outlet/write permission boundaries passed.');
    } finally { await new Promise(resolve => server.close(resolve)); }
    console.log('Billing panels passed: 105 searchable/scoped settlements, frozen closure/replay, 25 QR requests, outlet-scoped bill history/search/filter/paging, unchanged array API and waiter-owned history over authenticated HTTP.');
  } else if (process.argv[3] === 'billing-policy') {
    const load = relative => import(pathToFileURL(resolve(process.argv[2], relative)));
    const { getFulfillmentPolicy, assertFulfillmentEnabled } = await load('backend/src/core/billing/fulfillment-policy.js');
    const { billingService } = await load('backend/src/core/billing/billing.service.js');
    const { ordersService } = await load('backend/src/core/orders/orders.service.js');
    const policy = await getFulfillmentPolicy(businessId);
    const count = await prisma.bill.count({ where: { businessId } });
    for (const [mode, enabled] of Object.entries(policy)) {
      if (enabled) await assertFulfillmentEnabled(businessId, { service_mode: mode });
      else {
        await assert.rejects(billingService.createInvoice({ tenantId: 'coupled-tenant', user: { role: 'Owner' }, payload: { service_mode: mode } }), error => error.code === 'FULFILLMENT_MODE_DISABLED');
        await assert.rejects(billingService.createInvoice({ tenantId: 'coupled-tenant', user: { role: 'Owner' }, payload: { fulfillment_mode: mode } }), error => error.code === 'FULFILLMENT_MODE_DISABLED');
        await assert.rejects(ordersService.createOrder({ tenantId: 'coupled-tenant', actor: { role: 'Owner' }, payload: { metadata: { service_mode: mode } } }), error => error.code === 'FULFILLMENT_MODE_DISABLED');
      }
    }
    assert.equal(await prisma.bill.count({ where: { businessId } }), count);
    console.log('Persisted billing policy enforced by invoice and order services without creating rejected records.');
  } else if (process.argv[3] === 'erase') {
    const { customersService } = await import(pathToFileURL(resolve(process.argv[2], 'backend/src/core/customers/customers.service.js')));
    await customersService.anonymize({ businessId, customerId: 'fixture-customer', payload: { reason: 'Isolated paired acceptance request' } });
  } else {
  await prisma.$transaction(async tx => {
    await tx.business.create({ data: { id: businessId, tenantId: 'coupled-tenant', name: 'Coupled fixture' } });
    await tx.customer.create({ data: { id: 'fixture-customer', businessId, name: 'Fixture customer', phone: '0000000000' } });
    await tx.order.create({ data: { id: 'fixture-private-order', businessId, customerName: 'Fixture customer',
      channel: 'pos', total: 123.45, status: 'pending', metadata: { customer_id: 'fixture-customer',
        customer_phone: '0000000000', notes: 'Private kitchen note', delivery_address: 'Private delivery address' } } });
    await tx.kitchenTicket.create({ data: { id: 'fixture-kot', businessId, orderId: 'fixture-private-order', status: 'pending' } });
    await tx.bill.create({ data: { id: 'fixture-private-bill', businessId, orderId: 'fixture-private-order', customerName: 'Fixture customer', currency: 'INR', total: 123.45, status: 'paid',
      metadata: { customer_id: 'fixture-customer', customer_phone: '0000000000', customer_name: 'Fixture customer', delivery_address: 'Private delivery address' } } });
    await tx.tipPayout.create({ data: { id: 'fixture-payout', businessId, userName: 'Fixture staff', amount: 12.5, periodFrom: date, periodTo: date, method: 'cash' } });
    await tx.payrollRun.create({ data: { id: 'fixture-run', businessId, label: 'Fixture payroll', periodFrom: date, periodTo: date, status: 'finalized' } });
    await tx.payslip.create({ data: { id: 'fixture-payslip', businessId, runId: 'fixture-run', userName: 'Fixture staff', profile: { bankAccount: 'DO_NOT_EXPORT_PRIVATE_PROFILE' }, daysInPeriod: 1, daysPresent: 1, gross: 100, totalDeductions: 10, netPay: 90 } });
    await tx.loyaltyEntry.create({ data: { id: 'fixture-loyalty', businessId, customerId: 'fixture-customer', type: 'earn', points: 5, balanceAfter: 5 } });
    await tx.giftCard.create({ data: { id: 'fixture-card', businessId, code: 'DO_NOT_EXPORT_REDEMPTION_CODE', initialValue: 50, balance: 40, status: 'active' } });
    await tx.giftCardTransaction.create({ data: { id: 'fixture-card-transaction', businessId, giftCardId: 'fixture-card', type: 'redeem', amount: -10, balanceAfter: 40 } });
    await tx.marketingTemplate.create({ data: { id: 'fixture-template', businessId, channel: 'sms', name: 'Fixture', body: 'DO_NOT_EXPORT_MESSAGE_BODY' } });
    await tx.marketingCampaign.create({ data: { id: 'fixture-campaign', businessId, channel: 'sms', name: 'Fixture campaign', templateId: 'fixture-template', status: 'completed' } });
    await tx.marketingMessage.create({ data: { id: 'fixture-delivery', businessId, campaignId: 'fixture-campaign', customerId: 'fixture-customer', channel: 'sms', phone: '0000000000', body: 'DO_NOT_EXPORT_MESSAGE_BODY', error: 'DO_NOT_EXPORT_PROVIDER_ERROR', status: 'delivered', attempts: 1, deliveredAt: date } });
  });
  }
} finally {
  await prisma.$disconnect();
}
