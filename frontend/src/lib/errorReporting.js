import * as Sentry from "@sentry/react";

/**
 * Browser error reporting for AdminCore, on only when REACT_APP_SENTRY_DSN is set at build time.
 * No user details, IP addresses, request bodies or query strings are sent; emails, phone numbers and tokens in
 * messages are replaced, and keystroke/console breadcrumbs are dropped.
 */
const PATTERNS = [
  [/\bBearer\s+[A-Za-z0-9._~+/-]+=*/gi, "Bearer [redacted]"],
  [/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, "[redacted-jwt]"],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "[email]"],
  [/\+?\d(?:[\s-]?\d){7,}/g, "[number]"],
];
export const scrubText = (value) => {
  if (value === null || value === undefined) return value;
  let text = String(value);
  for (const [pattern, replacement] of PATTERNS) text = text.replace(pattern, replacement);
  return text;
};
const scrubUrl = (url) => scrubText(String(url || "").split(/[?#]/)[0]);

export const scrubEvent = (event) => {
  if (!event) return event;
  delete event.user;
  if (event.request) event.request = { url: event.request.url ? scrubUrl(event.request.url) : undefined };
  if (event.message) event.message = scrubText(event.message);
  for (const value of event.exception?.values || []) if (value.value) value.value = scrubText(value.value);
  if (event.breadcrumbs) {
    event.breadcrumbs = event.breadcrumbs
      .filter((crumb) => crumb.category !== "console" && crumb.category !== "ui.input")
      .map((crumb) => ({ ...crumb, message: crumb.message ? scrubText(crumb.message) : crumb.message,
        data: crumb.data?.url ? { url: scrubUrl(crumb.data.url), method: crumb.data.method, status_code: crumb.data.status_code } : undefined }));
  }
  delete event.extra;
  return event;
};

export const initErrorReporting = () => {
  const dsn = String(process.env.REACT_APP_SENTRY_DSN || "").trim();
  if (!dsn) return false;
  Sentry.init({ dsn, environment: process.env.REACT_APP_SENTRY_ENVIRONMENT || process.env.NODE_ENV, sendDefaultPii: false, tracesSampleRate: 0, maxBreadcrumbs: 30, beforeSend: scrubEvent });
  return true;
};
