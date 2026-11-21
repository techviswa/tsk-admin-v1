"""Error reporting to Sentry for AdminCore, on only when SENTRY_DSN is set.

Nothing personal is sent: no request bodies, cookies, headers, query strings, IP addresses, user emails or local
variables. Tokens, keys, emails and phone numbers inside messages are replaced. Expected HTTP errors (4xx) are dropped.
"""
import os
import re

_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]+=*", re.I), "Bearer [redacted]"),
    (re.compile(r"\b(rzp_(?:live|test)_)[A-Za-z0-9]+"), r"\1[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[redacted-jwt]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"\+?\d(?:[\s-]?\d){7,}"), "[number]"),
]


def scrub_text(value):
    if value is None:
        return value
    text = str(value)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub_event(event, hint=None):
    exc = (hint or {}).get("exc_info")
    if exc:
        status = getattr(exc[1], "status_code", None)
        if isinstance(status, int) and status < 500:
            return None  # an expected client error, not something to alert on
    event.pop("user", None)
    event.pop("server_name", None)
    request = event.get("request") or {}
    if request:
        url = str(request.get("url") or "").split("?")[0]
        event["request"] = {"method": request.get("method"), "url": scrub_text(url)}
    if event.get("message"):
        event["message"] = scrub_text(event["message"])
    if isinstance(event.get("logentry"), dict):
        event["logentry"] = {"message": scrub_text(event["logentry"].get("message")), "params": []}
    for value in (event.get("exception") or {}).get("values", []) or []:
        if value.get("value"):
            value["value"] = scrub_text(value["value"])
        for frame in (value.get("stacktrace") or {}).get("frames", []) or []:
            for key in ("vars", "pre_context", "context_line", "post_context"):
                frame.pop(key, None)
    crumbs = (event.get("breadcrumbs") or {}).get("values") if isinstance(event.get("breadcrumbs"), dict) else None
    if crumbs is not None:
        event["breadcrumbs"]["values"] = [{**crumb, "message": scrub_text(crumb.get("message")), "data": None} for crumb in crumbs]
    event.pop("extra", None)
    return event


def init_error_reporting(environ=os.environ) -> bool:
    dsn = (environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    import sentry_sdk
    sentry_sdk.init(
        dsn=dsn,
        environment=environ.get("SENTRY_ENVIRONMENT") or environ.get("ENVIRONMENT") or "production",
        release=environ.get("SENTRY_RELEASE") or environ.get("RENDER_GIT_COMMIT") or None,
        send_default_pii=False,
        include_local_variables=False,
        include_source_context=False,
        max_request_body_size="never",
        traces_sample_rate=0,
        before_send=scrub_event,
    )
    return True
