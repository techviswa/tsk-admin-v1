import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import DashboardPage, { formatStatValue } from './DashboardPage';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api from '@/lib/api';
jest.mock('@/components/layout/DashboardLayout', () => ({ useBusiness: jest.fn() }));
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn() }, formatApiError: String }));

test('unavailable or invalid dashboard values are never invented zeroes', () => {
  expect(formatStatValue(null, {})).toBe('—');
  expect(formatStatValue(undefined, {})).toBe('—');
  expect(formatStatValue('NaN', {})).toBe('—');
  expect(formatStatValue(0, {})).toBe('0');
});

test('dashboard money retains cents and the actual source currency', () => {
  expect(formatStatValue(12.45, { currency: true, currencyCode: 'USD' })).toContain('12.45');
  expect(formatStatValue(12.45, { currency: true, currencyCode: 'USD' })).not.toContain('₹');
  expect(formatStatValue(12.45, { currency: true, currencyCode: 'INR' })).toContain('₹');
  expect(formatStatValue(12.45, { currency: true, currencyCode: 'unspecified' })).toBe('12.45');
});

test('overview failures retain unknown values, retry recovers, and metadata refreshes do not refetch', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const element = document.createElement('div'), root = createRoot(element);
  const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
  useBusiness.mockReturnValue({ selectedBusiness: null });
  api.get.mockReset().mockRejectedValueOnce(new Error('Unavailable')).mockResolvedValue({ data: { total_businesses: 3, recent_activity: [] } });
  try {
    await act(async () => root.render(<DashboardPage />));
    expect(element.textContent).toContain('Overview could not refresh');
    expect(element.textContent).not.toMatch(/Recent Events0/);
    await act(async () => element.querySelector('button').click());
    expect(element.textContent).toContain('Platform Overview');
    expect(element.textContent).not.toContain('Overview could not refresh');
    useBusiness.mockReturnValue({ selectedBusiness: { id: 'business', name: 'Shop' } });
    await act(async () => root.render(<DashboardPage />));
    const requests = api.get.mock.calls.length;
    useBusiness.mockReturnValue({ selectedBusiness: { id: 'business', name: 'Renamed shop' } });
    await act(async () => root.render(<DashboardPage />));
    expect(api.get).toHaveBeenCalledTimes(requests);
    expect(element.textContent).toContain('Renamed shop');
  } finally { await act(async () => root.unmount()); consoleError.mockRestore(); }
});
