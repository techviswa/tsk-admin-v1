import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import FeatureFlagsPage from './FeatureFlagsPage';
import AuditLogsPage from './AuditLogsPage';
import SettingsPage from './SettingsPage';
import IntegrationsPage from './IntegrationsPage';
import api from '@/lib/api';

const mockBusiness = { id: 'business', name: 'Business' };
jest.mock('@/components/layout/DashboardLayout', () => ({ useBusiness: () => ({ selectedBusiness: mockBusiness }) }));
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { role: 'platform_admin' } }) }));
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn() }, formatApiError: String }));
jest.mock('sonner', () => ({ toast: {} }));

for (const [Page, emptyText] of [[FeatureFlagsPage, 'No feature flags'], [AuditLogsPage, 'No audit logs found'], [SettingsPage, 'Save Changes'], [IntegrationsPage, 'No integrations configured yet']]) {
  test(`${Page.name} distinguishes failed loading from a valid empty dataset`, async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockRejectedValue(new Error('Service unavailable'));
    const element = document.createElement('div'), root = createRoot(element);
    try {
      await act(async () => root.render(<Page />));
      expect(element.querySelector('[role="alert"]').textContent).toContain('Service unavailable');
      expect(element.textContent).not.toContain(emptyText);
      expect([...element.querySelectorAll('button')].some(button => button.textContent === 'Retry')).toBe(true);
    } finally { await act(async () => root.unmount()); }
  });
}
