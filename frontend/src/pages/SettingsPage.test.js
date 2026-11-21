import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import SettingsPage from './SettingsPage';
import api from '@/lib/api';
const mockBusiness = { id: 'business', name: 'Business' };
jest.mock('@/components/layout/DashboardLayout', () => ({ useBusiness: () => ({ selectedBusiness: mockBusiness }) }));
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn() }, formatApiError: String }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn(), info: jest.fn() } }));
jest.mock('@/components/ui/tabs', () => ({ Tabs: ({ children }) => <div>{children}</div>, TabsList: ({ children }) => <div>{children}</div>, TabsTrigger: ({ children }) => <span>{children}</span>, TabsContent: ({ children }) => <section>{children}</section> }));
test('notification changes submit the new value instead of the previous render value', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockResolvedValue({ data: [{ key: 'email_notifications', value: 'false', category: 'notifications' }] });
  api.put.mockResolvedValue({ data: {} });
  const element = document.createElement('div'), root = createRoot(element);
  try {
    await act(async () => root.render(<SettingsPage />));
    await act(async () => element.querySelector('[data-testid="setting-toggle-email_notifications"]').click());
    expect(api.put).toHaveBeenCalledWith('/settings/business/business/email_notifications', { value: 'true' });
    await act(async () => element.querySelector('[data-testid="setting-toggle-email_notifications"]').click());
    expect(api.put).toHaveBeenLastCalledWith('/settings/business/business/email_notifications', { value: 'false' });
  } finally { await act(async () => root.unmount()); }
});

test('failed preference changes retain the last confirmed value and offer reload', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockResolvedValue({ data: [{ key: 'email_notifications', value: 'false', category: 'notifications' }] });
  api.put.mockRejectedValue(new Error('Not confirmed'));
  const element = document.createElement('div'), root = createRoot(element);
  try {
    await act(async () => root.render(<SettingsPage />));
    await act(async () => element.querySelector('[data-testid="setting-toggle-email_notifications"]').click());
    expect(element.querySelector('[data-testid="setting-toggle-email_notifications"]').getAttribute('aria-checked')).toBe('false');
    expect(element.querySelector('[role="alert"]').textContent).toContain('could not be confirmed');
  } finally { await act(async () => root.unmount()); }
});
