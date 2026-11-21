import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import POSOperationsPage from './POSOperationsPage';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api from '@/lib/api';
import { displayValue, sourceValue } from '@/lib/posOperations';

jest.mock('react-router-dom', () => ({ useParams: () => ({ resource: 'gift-cards' }) }), { virtual: true });
jest.mock('@/components/layout/DashboardLayout', () => ({ useBusiness: jest.fn() }));
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() }, formatApiError: String }));

let root, element, business;
const page = (name, hasMore = false) => ({ data: { records: [{ id: name, title: name, status: 'active', metadata: { masked_code: name, balance: 0 } }], summary: { total: 51 }, pagination: { total: 51, has_more: hasMore } } });
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  business = null;
  api.get.mockReset(); api.post.mockReset();
  useBusiness.mockImplementation(() => ({ selectedBusiness: business }));
  element = document.createElement('div'); root = createRoot(element);
});
afterEach(async () => { await act(async () => root.unmount()); });

test('requires a business and never requests cross-business operational data', async () => {
  await act(async () => root.render(<POSOperationsPage />));
  expect(element.textContent).toContain('Select a business');
  expect(api.get).not.toHaveBeenCalled();
});

test('business switch hides old rows and ignores an old in-flight response', async () => {
  let old;
  business = { id: 'a', name: 'A' };
  api.get.mockReturnValueOnce(new Promise(resolve => { old = resolve; }));
  await act(async () => root.render(<POSOperationsPage />));
  business = { id: 'b', name: 'B' };
  api.get.mockResolvedValueOnce(page('B card'));
  await act(async () => root.render(<POSOperationsPage />));
  await act(async () => old(page('Private A card')));
  expect(element.textContent).toContain('B card');
  expect(element.textContent).not.toContain('Private A card');
  expect(api.get.mock.calls[1][1].params.business_id).toBe('b');
});

test('pages records without exposing local write actions', async () => {
  business = { id: 'a', name: 'A' };
  api.get.mockResolvedValueOnce(page('First card', true)).mockResolvedValueOnce(page('Second card'));
  await act(async () => root.render(<POSOperationsPage />));
  expect(element.textContent).toContain('0.00');
  expect(element.textContent).not.toContain('New Record');
  await act(async () => [...element.querySelectorAll('button')].find(button => button.textContent === 'Next').click());
  expect(api.get.mock.calls[1][1].params).toMatchObject({ business_id: 'a', limit: 50, offset: 50 });
  expect(element.textContent).toContain('Second card');
});

test('formats zero and false source values and removes nested secrets', () => {
  expect(sourceValue({ amount: 10, metadata: { amount: 0 } }, 'amount')).toBe(0);
  expect(displayValue(false)).toBe('No');
  expect(displayValue({ metadata: { token: 'private', public: 0 } })).not.toContain('private');
  expect(displayValue(0, 'money', 'USD')).toBe('0.00 USD');
  expect(displayValue(10, 'money')).toContain('currency not provided');
});
