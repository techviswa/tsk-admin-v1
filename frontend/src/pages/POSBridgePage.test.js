import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import POSBridgePage from './POSBridgePage';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api from '@/lib/api';

jest.mock('@/components/layout/DashboardLayout', () => ({ useBusiness: jest.fn() }));
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn() }, formatApiError: String }));
jest.mock('sonner', () => ({ toast: { error: jest.fn() } }));
let root, element, business;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  business = { id: 'a', name: 'A' };
  useBusiness.mockImplementation(() => ({ selectedBusiness: business }));
  element = document.createElement('div'); root = createRoot(element);
});
afterEach(async () => { await act(async () => root.unmount()); });
const resource = { key: 'products', label: 'Products', endpoint: 'products', count: 1 };
const regular = url => Promise.resolve({ data: url.endsWith('/config') ? { configured: true } : url.endsWith('/resources') ? [resource] : [] });
test('late live previews cannot expose the previous business after switching', async () => {
  let oldPreview;
  api.get.mockImplementation(url => url.includes('/proxy/') ? new Promise(resolve => { oldPreview = resolve; }) : regular(url));
  await act(async () => root.render(<POSBridgePage />));
  await act(async () => [...element.querySelectorAll('button')].find(button => button.textContent === 'Live').click());
  business = { id: 'b', name: 'B' };
  await act(async () => root.render(<POSBridgePage />));
  await act(async () => oldPreview({ data: { rows: [{ name: 'Private A product' }] } }));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.body.textContent).not.toContain('Private A product');
});
test('incomplete connections cannot issue manual source requests', async () => {
  business = { id: 'a', name: 'A', pos_connection_state: 'setup_required' };
  api.get.mockImplementation(regular);
  await act(async () => root.render(<POSBridgePage />));
  for (const label of ['Live', 'Sync', 'Sync All', 'Sync feature access']) {
    expect([...element.querySelectorAll('button')].find(button => button.textContent === label).disabled).toBe(true);
  }
});
