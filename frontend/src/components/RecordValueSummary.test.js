import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import RecordValueSummary from './RecordValueSummary';

test('financial summaries retain legitimate zero amounts and keep different currencies separate', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const element = document.createElement('div'), root = createRoot(element);
  try {
    await act(async () => root.render(<RecordValueSummary monetary label="Invoice totals" summary={{ total: 1, amount_total: 0, amount_by_currency: [{ currency: 'USD', amount: 0 }] }} />));
    expect(element.textContent).toContain('0.00 USD');
    await act(async () => root.render(<RecordValueSummary monetary label="Invoice totals" summary={{ total: 2, amount_total: null, amount_by_currency: [{ currency: 'USD', amount: 10 }, { currency: 'INR', amount: 20 }] }} />));
    expect(element.textContent).toContain('Multiple currencies');
    expect(element.textContent).toContain('10.00 USD');
    expect(element.textContent).toContain('20.00 INR');
  } finally { await act(async () => root.unmount()); }
});
