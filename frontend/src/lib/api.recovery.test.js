jest.mock('axios', () => {
  const client = jest.fn();
  client.interceptors = { request: { use: jest.fn() }, response: { use: jest.fn() } };
  return { create: jest.fn(() => client), get: jest.fn(), post: jest.fn() };
});
import axios from 'axios';
import api, { clearAuthTokens, formatApiError } from './api';

test('structured errors show the actionable message without upstream diagnostics', () => {
  expect(formatApiError({ response: { data: { detail: {
    code: 'POS_BRIDGE_UNAUTHORIZED', message: 'Check the POS connection settings, then retry.',
    url: 'private-upstream-url',
  } } } })).toBe('Check the POS connection settings, then retry.');
});

const onError = api.interceptors.response.use.mock.calls[0][1];
beforeEach(() => { axios.get.mockReset(); axios.post.mockReset(); api.mockReset(); localStorage.clear(); });

test('concurrent failed reads share one readiness check and retry once', async () => {
  let ready;
  axios.get.mockImplementation(() => new Promise(resolve => { ready = resolve; }));
  api.mockResolvedValue({ data: 'recovered' });
  const failures = ['/products', '/outlets'].map(url => ({ config: { method: 'get', url }, response: { status: 503 } }));
  const pending = failures.map(onError);
  expect(axios.get).toHaveBeenCalledTimes(1);
  ready({ status: 200 });
  await Promise.all(pending);
  expect(api).toHaveBeenCalledTimes(2);
  await expect(onError(failures[0])).rejects.toBe(failures[0]);
});

test('a timed-out payment or login is never automatically replayed', async () => {
  for (const url of ['/payments', '/auth/login']) {
    const error = { config: { method: 'post', url }, code: 'ECONNABORTED' };
    await expect(onError(error)).rejects.toBe(error);
  }
  expect(axios.get).not.toHaveBeenCalled();
  expect(api).not.toHaveBeenCalled();
});

test('an expired session is refreshed once through the cookie, then the request is retried', async () => {
  axios.post.mockResolvedValueOnce({ data: { message: 'Token refreshed' } });
  api.mockResolvedValueOnce({ data: 'ok' });
  await expect(onError({ config: { method: 'get', url: '/products' }, response: { status: 401 } })).resolves.toEqual({ data: 'ok' });
  expect(axios.post.mock.calls[0][2]).toMatchObject({ withCredentials: true, headers: { 'X-Requested-With': 'XMLHttpRequest' } });
  axios.post.mockRejectedValueOnce({ response: { status: 401 } });
  await expect(onError({ config: { method: 'get', url: '/products' }, response: { status: 401 } })).rejects.toMatchObject({ response: { status: 401 } });
});

test('no session token is ever kept in browser storage', () => {
  localStorage.setItem('admincore_access_token', 'legacy');
  localStorage.setItem('admincore_refresh_token', 'legacy');
  clearAuthTokens();
  expect(localStorage.getItem('admincore_access_token')).toBeNull();
  expect(localStorage.getItem('admincore_refresh_token')).toBeNull();
});
