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

function compact(value) {
  return String(value ?? '').trim();
}

function formatDetailValue(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
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
  if (formatted) return formatted;

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// Format API error detail for display
export function formatApiError(err) {
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
    return parts.map(compact).filter(Boolean).join(' - ');
  }
  return formatDetailValue(detail);
}

export default api;
