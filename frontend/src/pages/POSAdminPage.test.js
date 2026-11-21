import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import POSAdminPage from './POSAdminPage';
import api from '@/lib/api';
import { useBusiness } from '@/components/layout/DashboardLayout';

jest.mock('react-router-dom', () => ({ useParams: () => ({ resource: 'inventory' }) }), { virtual: true });
jest.mock('@/components/layout/DashboardLayout', () => ({ useBusiness: jest.fn() }));
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn() }, formatApiError: String }));
jest.mock('sonner', () => ({ toast: { error: jest.fn() } }));
let root, element, context;
const page = (name, total = 501) => ({ data: { records: [{ id: name, title: name, status: 'active' }], statuses: ['active'], summary: { total, status_counts: [] }, pagination: { total, has_more: true } } });
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  context = { businesses: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }], selectedBusiness: { id: 'a', name: 'A' } };
  useBusiness.mockImplementation(() => context);
  element = document.createElement('div'); root = createRoot(element);
});
afterEach(async () => { await act(async () => root.unmount()); });
test('records beyond the old 500-row display cap are reachable and business changes reset the page', async () => {
  api.get.mockImplementation(url => Promise.resolve(url.startsWith('/outlets/') ? { data: [] } : page('Stock')));
  await act(async () => root.render(<POSAdminPage />));
  await act(async () => [...element.querySelectorAll('button')].find(button => button.textContent === 'Next').click());
  expect(api.get).toHaveBeenLastCalledWith('/pos-admin/inventory', expect.objectContaining({ params: expect.objectContaining({ limit: 50, offset: 50 }) }));
  context = { ...context, selectedBusiness: { id: 'b', name: 'B' } };
  await act(async () => root.render(<POSAdminPage />));
  expect(api.get).toHaveBeenLastCalledWith('/pos-admin/inventory', expect.objectContaining({ params: expect.objectContaining({ business_id: 'b', offset: 0 }) }));
});
test('late inventory details cannot reopen the old business record after a scope change', async () => {
  let resolveDetail;
  api.get.mockImplementation(url => url.endsWith('/movements') ? new Promise(resolve => { resolveDetail = resolve; }) : Promise.resolve(url.startsWith('/outlets/') ? { data: [] } : page('Stock')));
  await act(async () => root.render(<POSAdminPage />));
  await act(async () => element.querySelector('[aria-label="Edit Stock"]').click());
  context = { ...context, selectedBusiness: { id: 'b', name: 'B' } };
  await act(async () => root.render(<POSAdminPage />));
  await act(async () => resolveDetail({ data: [] }));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});
