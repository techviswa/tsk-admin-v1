import axios from 'axios';

const API_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: `${API_URL}/api`,
  withCredentials: true,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// Format API error detail for display
export function formatApiError(err) {
  const detail = err?.response?.data?.detail;
  if (!detail) return err?.message || 'Something went wrong';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e => e?.msg || JSON.stringify(e)).join(' ');
  if (detail?.msg) return detail.msg;
  if (detail?.code) {
    const parts = [detail.code];
    if (detail.module) parts.push(`module: ${detail.module}`);
    if (detail.feature) parts.push(`feature: ${detail.feature}`);
    if (detail.limit) parts.push(`limit: ${detail.limit}`);
    if (detail.currentPlan) parts.push(`plan: ${detail.currentPlan}`);
    if (detail.detail) parts.push(detail.detail);
    return parts.join(' - ');
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

export default api;
