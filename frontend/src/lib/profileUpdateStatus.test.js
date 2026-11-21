import { profileUpdateBlocked, profileUpdateMessage } from './profileUpdateStatus';

test('unfinished updates block a second credential change', () => {
  for (const pos_update_status of ['pending', 'running', 'retrying', 'failed']) {
    expect(profileUpdateBlocked({ pos_update_status })).toBe(true);
  }
  expect(profileUpdateBlocked({ pos_update_status: 'synced' })).toBe(false);
  expect(profileUpdateBlocked({ pos_update_status: 'rejected' })).toBe(false);
  expect(profileUpdateBlocked(null)).toBe(false);
});

test('retry notices distinguish queued work from saved credentials', () => {
  const message = profileUpdateMessage({ pos_update_status: 'retrying', pos_update_error: 'POS is rate limiting requests', pos_update_retry_at: '2026-10-23T12:00:00Z' });
  expect(message).toContain('has not been updated');
  expect(message).toContain('Next attempt:');
  expect(message).toContain('POS is rate limiting requests');
  expect(profileUpdateMessage({ pos_update_status: 'failed' })).toContain('Retry that update');
  expect(profileUpdateMessage({ pos_update_status: 'synced' })).toBe('');
  expect(profileUpdateMessage({ pos_update_status: 'rejected' })).toContain('Correct the details');
});
