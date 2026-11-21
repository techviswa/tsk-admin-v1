import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import OutletsPage from './OutletsPage';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api from '@/lib/api';
import { toast } from 'sonner';

jest.mock('@/components/layout/DashboardLayout', () => ({ useBusiness: jest.fn() }));
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { role: 'platform_admin' } }) }));
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() }, formatApiError: String }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn(), info: jest.fn() } }));

let root, element, context;
const reply = (name, extra = {}, total = 1) => ({ data: [{ id: name, name, code: 'CODE', status: 'active', pos_external_id: 'source', pos_metadata: { updated_at: 'version-1' }, ...extra }], headers: { 'x-total-count': String(total) } });
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  context = { selectedBusiness: { id: 'a', name: 'Business A' } };
  useBusiness.mockImplementation(() => context);
  element = document.createElement('div'); root = createRoot(element);
});
afterEach(async () => { await act(async () => root.unmount()); });

test.each([null, undefined])('opening Outlets without a selected business (%s) does not dereference an empty snapshot', async selectedBusiness => {
  context = { selectedBusiness };
  await act(async () => root.render(<OutletsPage />));
  expect(element.textContent).toContain('Select a Business');
  expect(api.get).not.toHaveBeenCalled();
  api.get.mockResolvedValue(reply('Selected outlet'));
  context = { selectedBusiness: { id: 'a', name: 'Business A' } };
  await act(async () => root.render(<OutletsPage />));
  expect(element.textContent).toContain('Selected outlet');
  context = { selectedBusiness: null };
  await act(async () => root.render(<OutletsPage />));
  expect(element.textContent).toContain('Select a Business');
  expect(element.textContent).not.toContain('Selected outlet');
});

test('clearing business selection while an outlet request is pending ignores its late response', async () => {
  let finish;
  api.get.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => root.render(<OutletsPage />));
  context = { selectedBusiness: null };
  await act(async () => root.render(<OutletsPage />));
  await act(async () => finish(reply('Previous private outlet')));
  expect(element.textContent).toContain('Select a Business');
  expect(element.textContent).not.toContain('Previous private outlet');
});

test('business changes cannot display a late outlet response from another business', async () => {
  let resolveA;
  api.get.mockImplementation(url => url.endsWith('/a') ? new Promise(resolve => { resolveA = resolve; }) : Promise.resolve(reply('B outlet')));
  await act(async () => root.render(<OutletsPage />));
  context = { selectedBusiness: { id: 'b', name: 'Business B' } };
  await act(async () => root.render(<OutletsPage />));
  await act(async () => resolveA(reply('Private A outlet')));
  expect(element.textContent).toContain('B outlet');
  expect(element.textContent).not.toContain('Private A outlet');
});

test('outlet pagination resets when the selected business changes', async () => {
  api.get.mockResolvedValue(reply('Outlet', {}, 101));
  await act(async () => root.render(<OutletsPage />));
  await act(async () => [...element.querySelectorAll('button')].find(button => button.textContent === 'Next').click());
  expect(api.get).toHaveBeenLastCalledWith('/outlets/business/a', { params: { limit: 50, offset: 50, status: 'active' } });
  context = { selectedBusiness: { id: 'b', name: 'Business B' } };
  await act(async () => root.render(<OutletsPage />));
  expect(api.get).toHaveBeenLastCalledWith('/outlets/business/b', { params: { limit: 50, offset: 0, status: 'active' } });
});

test('a queued status change remains visibly unconfirmed and cannot be submitted again', async () => {
  api.get.mockResolvedValueOnce(reply('Original outlet')).mockResolvedValue(reply('Original outlet', { pos_sync_pending: true, pos_command_status: 'pending' }));
  api.put.mockResolvedValue({ status: 202 });
  await act(async () => root.render(<OutletsPage />));
  await act(async () => element.querySelector('[role="button"]').click());
  expect(api.put).toHaveBeenCalledWith('/outlets/Original outlet', { status: 'inactive', expected_updated_at: 'version-1' });
  expect(toast.info).toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
  expect(element.textContent).toContain('Waiting for POS');
  expect(element.querySelector('[role="button"]').textContent).toBe('active');
  await act(async () => element.querySelector('[role="button"]').click());
  expect(api.put).toHaveBeenCalledTimes(1);
});

test('a failed command keeps its confirmed outlet and offers an explicit retry', async () => {
  api.get.mockResolvedValue(reply('Saved outlet', { pos_sync_pending: true, pos_command_status: 'failed', pos_sync_error: { message: 'POS update could not be confirmed' } }));
  api.post.mockResolvedValue({ status: 202 });
  await act(async () => root.render(<OutletsPage />));
  expect(element.textContent).toContain('Saved outlet');
  expect(element.textContent).toContain('Sync failed');
  await act(async () => [...element.querySelectorAll('button')].find(button => button.textContent === 'Retry sync').click());
  expect(api.post).toHaveBeenCalledWith('/outlets/Saved outlet/retry-pos');
});

test('status filtering resets pagination and asks the API for historical outlets explicitly', async () => {
  api.get.mockResolvedValue(reply('Outlet', {}, 101));
  await act(async () => root.render(<OutletsPage />));
  await act(async () => [...element.querySelectorAll('button')].find(button => button.textContent === 'Next').click());
  const select = element.querySelector('#outlet-status');
  await act(async () => { select.value = 'inactive'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(api.get).toHaveBeenLastCalledWith('/outlets/business/a', { params: { limit: 50, offset: 0, status: 'inactive' } });
});

test('partial business mappings keep source-dependent actions disabled', async () => {
  context.selectedBusiness = { ...context.selectedBusiness, pos_external_id: 'source', pos_connection_state: 'setup_required' };
  api.get.mockResolvedValue(reply('Saved outlet', { pos_external_id: null }));
  await act(async () => root.render(<OutletsPage />));
  for (const label of ['Refresh from POS', 'Review mapping']) {
    expect([...element.querySelectorAll('button')].find(button => button.textContent === label).disabled).toBe(true);
  }
  await act(async () => element.querySelector('[role="button"]').click());
  expect(api.put).not.toHaveBeenCalled();
});
