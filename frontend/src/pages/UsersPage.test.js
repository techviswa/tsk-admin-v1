import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import UsersPage from './UsersPage';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api from '@/lib/api';

jest.mock('@/components/layout/DashboardLayout', () => ({ useBusiness: jest.fn() }));
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 'admin', role: 'platform_admin' } }) }));
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn() }, formatApiError: String }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const business = { id: 'a', name: 'Restaurant A', type: 'restaurant', status: 'active' };
let root, element, context;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockReset();
  context = { selectedBusiness: null, businesses: [business], businessesLoaded: true, selectBusiness: jest.fn() };
  useBusiness.mockImplementation(() => context);
  element = document.createElement('div');
  root = createRoot(element);
});
afterEach(async () => { await act(async () => root.unmount()); });

test('all businesses shows business rows without fetching or exposing staff', async () => {
  await act(async () => root.render(<UsersPage />));
  expect(element.textContent).toContain('Restaurant A');
  expect(element.textContent).not.toContain('Add User');
  expect(api.get).not.toHaveBeenCalled();
  await act(async () => element.querySelector('button').click());
  expect(context.selectBusiness).toHaveBeenCalledWith(business);
});

test('team requests are scoped and late responses cannot replace the all-businesses view', async () => {
  let resolve;
  api.get.mockReturnValue(new Promise(done => { resolve = done; }));
  context = { ...context, selectedBusiness: business };
  await act(async () => root.render(<UsersPage />));
  expect(api.get).toHaveBeenCalledWith('/users', { params: { business_id: 'a' } });
  context = { ...context, selectedBusiness: null };
  await act(async () => root.render(<UsersPage />));
  await act(async () => resolve({ data: [{ id: 'staff', name: 'Hidden staff', role: 'staff' }] }));
  expect(element.textContent).toContain('Restaurant A');
  expect(element.textContent).not.toContain('Hidden staff');
  expect(api.get).toHaveBeenCalledTimes(1);
});

test('a selected business shows its team without platform accounts', async () => {
  context = { ...context, selectedBusiness: business };
  api.get.mockResolvedValue({ data: [
    { id: 'chef-a', name: 'Restaurant A chef', email: 'chef@example.test', role: 'staff', status: 'active' },
    { id: 'admin', name: 'Platform account', role: 'platform_admin' },
  ] });
  await act(async () => root.render(<UsersPage />));
  expect(element.textContent).toContain('Business team for Restaurant A');
  expect(element.textContent).toContain('Restaurant A chef');
  expect(element.textContent).not.toContain('Platform account');
  expect(element.textContent).toContain('Add User');
});
