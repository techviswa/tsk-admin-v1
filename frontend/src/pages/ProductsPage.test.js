import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ProductsPage from './ProductsPage';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api from '@/lib/api';

jest.mock('@/components/layout/DashboardLayout', () => ({ useBusiness: jest.fn() }));
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { role: 'platform_admin' } }) }));
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn() }, formatApiError: String }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn(), info: jest.fn() } }));

let root, element, context;
const reply = (name, total = 1) => ({ data: [{ id: name, name, price: 12, stock: 0 }], headers: { 'x-total-count': String(total) } });
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockReset();
  api.put.mockReset();
  context = { selectedBusiness: { id: 'a', name: 'Business A' } };
  useBusiness.mockImplementation(() => context);
  element = document.createElement('div');
  root = createRoot(element);
});
afterEach(async () => { await act(async () => root.unmount()); });

test('incomplete business connections cannot change product status or start source actions', async () => {
  context.selectedBusiness = { ...context.selectedBusiness, pos_connection_state: 'setup_required' };
  api.get.mockResolvedValue(reply('Saved product'));
  await act(async () => root.render(<ProductsPage />));
  const badge = element.querySelector('[aria-disabled="true"]');
  await act(async () => badge.click());
  expect(api.put).not.toHaveBeenCalled();
  expect([...element.querySelectorAll('button')].find(button => button.textContent === 'Refresh from POS').disabled).toBe(true);
  expect(element.querySelector('[data-testid="add-product-btn"]').disabled).toBe(true);
});

test('switching business ignores a late response from the previous business', async () => {
  let resolveA;
  api.get.mockImplementation((url, { params }) => params.business_id === 'a' ? new Promise(resolve => { resolveA = resolve; }) : Promise.resolve(reply('Business B item')));
  await act(async () => root.render(<ProductsPage />));
  context = { selectedBusiness: { id: 'b', name: 'Business B' } };
  await act(async () => root.render(<ProductsPage />));
  await act(async () => resolveA(reply('Private Business A item')));
  expect(element.textContent).toContain('Business B item');
  expect(element.textContent).not.toContain('Private Business A item');
});

test('paging fetches the next source page and switching business resets its offset', async () => {
  api.get.mockImplementation((url, { params }) => Promise.resolve(reply(`${params.business_id} page ${params.offset}`, 101)));
  await act(async () => root.render(<ProductsPage />));
  const next = [...element.querySelectorAll('button')].find(button => button.textContent === 'Next');
  expect(next).toBeDefined();
  await act(async () => next.click());
  expect(api.get).toHaveBeenLastCalledWith('/products', { params: { business_id: 'a', limit: 50, offset: 50 } });
  context = { selectedBusiness: { id: 'b', name: 'Business B' } };
  await act(async () => root.render(<ProductsPage />));
  expect(api.get).toHaveBeenLastCalledWith('/products', { params: { business_id: 'b', limit: 50, offset: 0 } });
  expect(element.textContent).not.toContain('a page 50');
});
