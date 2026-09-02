import axios from 'axios';

const DEFAULT_BACKEND_URL =
  process.env.NODE_ENV === 'production'
    ? 'https://tsk-admin-v1.onrender.com'
    : 'http://localhost:8000';

const API_URL = (process.env.REACT_APP_BACKEND_URL || DEFAULT_BACKEND_URL).replace(/\/$/, '');

const api = axios.create({
  baseURL: `${API_URL}/api`,
  withCredentials: true,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

const ACCESS_TOKEN_KEY = 'admincore_access_token';
const REFRESH_TOKEN_KEY = 'admincore_refresh_token';

export function setAuthTokens(accessToken, refreshToken) {
  if (accessToken) localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  if (refreshToken) localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearAuthTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(ACCESS_TOKEN_KEY);
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  response => response,
  async (error) => {
    const original = error.config;
    const status = error?.response?.status;
    const isAuthRequest = original?.url?.startsWith('/auth/login') || original?.url?.startsWith('/auth/register') || original?.url?.startsWith('/auth/refresh');
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);

    if (status === 401 && original && !original._retry && !isAuthRequest && refreshToken) {
      original._retry = true;
      try {
        const { data } = await axios.post(`${API_URL}/api/auth/refresh`, null, {
          withCredentials: true,
          headers: { 'X-Refresh-Token': refreshToken },
          timeout: 15000,
        });
        setAuthTokens(data.access_token, data.refresh_token);
        original.headers = original.headers || {};
        original.headers.Authorization = `Bearer ${data.access_token}`;
        return api(original);
      } catch (refreshError) {
        clearAuthTokens();
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

// Format API error detail for display
export function formatApiError(err) {
  if (err?.code === 'ECONNABORTED') {
    return 'Request timed out. The server did not finish in time; retry after the backend finishes waking up or check the sync health details.';
  }
  if (err?.message === 'Network Error' && !err?.response) {
    return 'Cannot reach AdminCore backend. Check that the backend deploy is running and the frontend backend URL/CORS settings match.';
  }
  const detail = err?.response?.data?.detail;
  if (!detail) return err?.message || 'Something went wrong';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e => e?.msg || formatDetailValue(e)).join(' ');
  if (detail?.msg) return detail.msg;
  if (detail?.code) {
    const parts = [detail.code];
    if (detail.module) parts.push(`module: ${detail.module}`);
    if (detail.feature) parts.push(`feature: ${detail.feature}`);
    if (detail.limit) parts.push(`limit: ${detail.limit}`);
    if (detail.currentPlan) parts.push(`plan: ${detail.currentPlan}`);
    if (detail.message) parts.push(formatDetailValue(detail.message));
    if (detail.detail) parts.push(formatDetailValue(detail.detail));
    return shorten(parts.map(compact).filter(Boolean).join(' - '));
  }
  return shorten(formatDetailValue(detail));
}

export default api;
