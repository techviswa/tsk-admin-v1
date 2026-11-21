import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import api from '@/lib/api';
import FulfillmentModeControls from './FulfillmentModeControls';
jest.mock('@/lib/api', () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn() }, formatApiError: e => e.message }));
test('all-business view keeps the billing controls discoverable without implying a business policy', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const element = document.createElement('div'), root = createRoot(element);
  try {
    await act(async () => root.render(<FulfillmentModeControls business={null} />));
    expect(element.querySelector('h2').textContent).toBe('POS billing modes');
    expect([...element.querySelectorAll('label')].map(label => label.textContent)).toEqual(['Table', 'Token', 'Pickup', 'Delivery']);
    expect([...element.querySelectorAll('input')].every(input => input.disabled)).toBe(true);
    expect(element.querySelector('[role="status"]').textContent).toContain('No business selected');
  } finally { await act(async () => root.unmount()); }
});
test('business mode changes submit the saved version and show pending confirmation', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const modes = { TABLE: true, TOKEN: true, PICKUP: true, DELIVERY: true };
  api.get.mockResolvedValue({ data: { modes, version: 2, connected: true, sync_status: 'confirmed' } });
  api.put.mockResolvedValue({ data: { modes: { ...modes, TABLE: false }, version: 3, connected: true, sync_status: 'pending' } });
  const element = document.createElement('div'), root = createRoot(element);
  try {
    await act(async () => root.render(<FulfillmentModeControls business={{ id: 'business', name: 'Restaurant' }} />));
    await act(async () => element.querySelector('input').click());
    await act(async () => element.querySelector('button').click());
    expect(api.put).toHaveBeenCalledWith('/businesses/business/fulfillment-modes', { modes: { ...modes, TABLE: false }, version: 2 });
    expect(element.textContent).toContain('confirmation is pending');
  } finally { await act(async () => root.unmount()); }
});
