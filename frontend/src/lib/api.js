import axios from 'axios';

// Both the development proxy and Vercel route /api to the backend. Keep cookies
// first-party so login also works when browsers block third-party cookies.
const DEFAULT_BACKEND_URL = '';

const API_URL = (process.env.REACT_APP_BACKEND_URL ?? DEFAULT_BACKEND_URL).replace(/\/$/, '');

// Sent on every request; the backend refuses cookie-authenticated writes without it (CSRF protection).
const CSRF_HEADERS = { 'X-Requested-With': 'XMLHttpRequest' };

const api = axios.create({
  baseURL: `${API_URL}/api`,
  withCredentials: true,
  timeout: 45000,
  headers: { 'Content-Type': 'application/json', ...CSRF_HEADERS },
});

// Sessions live only in httpOnly cookies that page scripts cannot read. Tokens stored by older builds are removed.
const LEGACY_TOKEN_KEYS = ['admincore_access_token', 'admincore_refresh_token'];
let wakeRequest;
const wakeBackend = () => {
  if (!wakeRequest) wakeRequest = axios.get(`${API_URL}/api/health`, { timeout: 65000 }).finally(() => { wakeRequest = null; });
  return wakeRequest;
};

export function clearAuthTokens() {
  try { LEGACY_TOKEN_KEYS.forEach((key) => localStorage.removeItem(key)); } catch { /* storage unavailable */ }
}

clearAuthTokens();

api.interceptors.response.use(
  response => response,
  async (error) => {
    const original = error.config;
    const status = error?.response?.status;
    const isAuthRequest = original?.url?.startsWith('/auth/login') || original?.url?.startsWith('/auth/register') || original?.url?.startsWith('/auth/refresh');
    const method = String(original?.method || 'get').toLowerCase();
    const retryableAfterWake = ['get', 'head', 'options'].includes(method);
    const connectionFailed = error?.code === 'ECONNABORTED'
      || (error?.message === 'Network Error' && !error?.response)
      || [502, 503, 504].includes(status);

    if (connectionFailed && original && !original._networkRetry && retryableAfterWake) {
      original._networkRetry = true;
      try {
        await wakeBackend();
        original.timeout = original.url?.startsWith('/auth/') ? 90000 : 45000;
        return api(original);
      } catch {
        return Promise.reject(error);
      }
    }

    if (status === 401 && original && !original._retry && !isAuthRequest) {
      original._retry = true;
      try {
        // The refresh cookie is sent automatically; the new session arrives as cookies too.
        await axios.post(`${API_URL}/api/auth/refresh`, null, {
          withCredentials: true,
          headers: CSRF_HEADERS,
          timeout: 15000,
        });
        return api(original);
      } catch (refreshError) {
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

function compact(value) {
  return String(value ?? '').trim();
}

function shorten(value, max = 500) {
  const text = compact(value);
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function stripHtmlError(value) {
  const text = compact(value);
  if (!/<\/?[a-z][\s\S]*>/i.test(text) && !text.toLowerCase().includes('<!doctype')) {
    return text;
  }
  const titleMatch = text.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  const title = titleMatch ? titleMatch[1].replace(/\s+/g, ' ').trim() : 'HTML error page';
  const body = text
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return `${title}${body ? `: ${body}` : ''}`;
}

function formatDetailValue(value) {
  if (!value) return '';
  if (typeof value === 'string') return shorten(stripHtmlError(value));
  if (Array.isArray(value)) {
    return value.map(formatDetailValue).filter(Boolean).join(' ');
  }
  if (typeof value !== 'object') return String(value);

  const parts = [];
  if (value.code) parts.push(value.code);
  if (value.status_code) parts.push(`HTTP ${value.status_code}`);
  if (value.context) parts.push(value.context);
  if (value.cause) parts.push(formatDetailValue(value.cause));
  if (value.message) parts.push(formatDetailValue(value.message));
  if (value.detail) parts.push(formatDetailValue(value.detail));
  if (value.response) parts.push(formatDetailValue(value.response));
  if (value.error) parts.push(formatDetailValue(value.error));
  if (value.url && parts.length < 3) parts.push(value.url);

  const formatted = parts.map(compact).filter(Boolean).join(' - ');
  if (formatted) return shorten(formatted);

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function formatApiDetail(value) {
  return shorten(formatDetailValue(value));
}

// Format API error detail for display
export function formatApiError(err) {
  if (err?.code === 'ECONNABORTED') {
    return 'The server may be waking up. Please try again shortly. Before repeating a save or payment, check whether it already completed.';
  }
  if (err?.message === 'Network Error' && !err?.response) {
    return 'Unable to reach AdminCore. Check your connection and try again shortly.';
  }
  const detail = err?.response?.data?.detail;
  if (!detail) return err?.message || 'Something went wrong';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e => e?.msg || formatDetailValue(e)).join(' ');
  if (detail?.msg) return detail.msg;
  if (detail?.message) return shorten(formatDetailValue(detail.message));
  if (detail?.code) {
    const parts = [detail.code];
    if (detail.module) parts.push(`module: ${detail.module}`);
    if (detail.feature) parts.push(`feature: ${detail.feature}`);
    if (detail.limit) parts.push(`limit: ${detail.limit}`);
    if (detail.currentPlan) parts.push(`plan: ${detail.currentPlan}`);
    if (detail.cause) parts.push(formatDetailValue(detail.cause));
    if (detail.message) parts.push(formatDetailValue(detail.message));
    if (detail.detail) parts.push(formatDetailValue(detail.detail));
    return shorten(parts.map(compact).filter(Boolean).join(' - '));
  }
  return shorten(formatDetailValue(detail));
}

export default api;
