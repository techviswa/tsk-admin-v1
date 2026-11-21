from dotenv import load_dotenv
from pathlib import Path
from default_outlet import create_default_outlet_once
from pos_passwords import hash_pos_password

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import logging
import secrets
import hmac
import hashlib
import json
import uuid
import requests
from fastapi.responses import JSONResponse
import asyncio
import base64
import re
import time
import math
import threading
from io import BytesIO
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel as SchemaModel, ConfigDict, Field
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError, DuplicateKeyError
import bcrypt
import jwt as pyjwt
import qrcode
from pos_sync_worker import PosSyncWorker
from pos_operations import OPERATIONS, register_operations, reconcile_snapshot
from pos_privacy import ProjectionPrivacy
from pos_export_client import await_export
from operations_health import queue_health
from pos_projection import summarize as summarize_pos_collection, require_local_write, payment_groups, report_filter, bill_revenue_expression, bill_revenue, kitchen_groups
from pos_reconciliation import PosReconciler, import_lease
from pos_access import tenant_read_scope, membership as pos_membership, management_role
from pos_product_commands import ProductCommands
from pos_profile_updates import PosProfileUpdates
from pos_entitlements import pos_entitlement_payload
from report_pdf import build_report, render_report_pdf, render_report_csv
from business_assignments import recover_business_assignments
from pos_retry import retry_after_seconds, rate_limit_delay
import mfa as mfa_lib
import subscription_lifecycle
import saas_billing
from platform_rbac import (
    ALL_ROLES, PLATFORM_ROLES, can_assign_role, has_platform_permission,
    is_platform_staff, platform_permissions_for,
)
from auth_guard import lockout_seconds_remaining, record_failed_attempt, clear_failed_attempts
from production_config import validate_production_config

# ===== CONFIGURATION =====
PRODUCTION_CONFIG_STATUS = validate_production_config(os.environ, raise_on_error=False)
if not PRODUCTION_CONFIG_STATUS.get("ok", True):
    logging.getLogger("admincore.config").error(
        "Production configuration needs attention: %s",
        "; ".join(PRODUCTION_CONFIG_STATUS.get("errors", [])),
    )
JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
POS_CORE_API_BASE_URL = os.environ.get("POS_CORE_API_BASE_URL", "").rstrip("/")
POS_CORE_API_KEY = os.environ.get("POS_CORE_API_KEY", "")
POS_CORE_OWNER_EMAIL = os.environ.get("POS_CORE_OWNER_EMAIL", "")
POS_CORE_OWNER_PASSWORD = os.environ.get("POS_CORE_OWNER_PASSWORD", "")
ADMINCORE_API_KEY = os.environ.get("ADMINCORE_API_KEY", "")
POS_CORE_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("POS_CORE_REQUEST_TIMEOUT_SECONDS", "20"))
POS_BRIDGE_RESOURCE_TIMEOUT_SECONDS = int(os.environ.get("POS_BRIDGE_RESOURCE_TIMEOUT_SECONDS", "45"))
POS_CORE_RATE_LIMIT_COOLDOWN_SECONDS = int(os.environ.get("POS_CORE_RATE_LIMIT_COOLDOWN_SECONDS", "300"))
POS_PROVISIONING_MAX_ATTEMPTS = int(os.environ.get("POS_PROVISIONING_MAX_ATTEMPTS", "6"))
POS_PROVISIONING_WORKER_ENABLED = os.environ.get("POS_PROVISIONING_WORKER_ENABLED", "true").lower() != "false"
POS_PROVISIONING_WORKER_INTERVAL_SECONDS = int(os.environ.get("POS_PROVISIONING_WORKER_INTERVAL_SECONDS", "30"))
POS_PROVISIONING_INITIAL_DELAY_SECONDS = int(os.environ.get("POS_PROVISIONING_INITIAL_DELAY_SECONDS", "20"))
POS_PROVISIONING_RETRY_DELAYS_SECONDS = [30, 60, 180, 300, 600, 900]
POS_CORE_RATE_LIMIT_UNTIL = 0.0
POS_CORE_REQUEST_INTERVAL_SECONDS = max(0.0, float(os.environ.get("POS_CORE_REQUEST_INTERVAL_SECONDS", "1")))
pos_request_lock = threading.Lock()
pos_next_request_at = 0.0

# ===== DATABASE =====
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(
    mongo_url,
    serverSelectionTimeoutMS=3000,
    connectTimeoutMS=3000,
    socketTimeoutMS=10000,
)
db = client[os.environ['DB_NAME']]

# ===== APP =====
from error_reporting import init_error_reporting

# Sentry (only when SENTRY_DSN is set), before the app is created so its errors are reported too.
init_error_reporting()
app = FastAPI(title="AdminCore API", version="1.0.0")
api_router = APIRouter(prefix="/api")

@api_router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "admincore",
        "environment": "production" if PRODUCTION_CONFIG_STATUS.get("production") else "development",
        "revision": os.environ.get("RENDER_GIT_COMMIT", "local"),
        "time": datetime.now(timezone.utc).isoformat(),
        "pos_configured": bool(POS_CORE_API_BASE_URL),
        "production_config_ok": PRODUCTION_CONFIG_STATUS.get("ok", True),
        # Configuration detail is reconnaissance material; the log carries it for operators.
        **({} if PRODUCTION_CONFIG_STATUS.get("production") else {"production_config_errors": PRODUCTION_CONFIG_STATUS.get("errors", [])}),
    }

@api_router.get("/ready")
async def readiness_check():
    if not PRODUCTION_CONFIG_STATUS.get("ok", True):
        raise HTTPException(status_code=503, detail="Service configuration requires attention")
    try:
        await db.command("ping")
    except PyMongoError:
        raise HTTPException(status_code=503, detail="Database is not ready")
    return {"ready": True}

# ===== LOGGING =====
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ===================================================================
# AUTH UTILITIES
# ===================================================================
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

def create_access_token(user_id: str, email: str, token_version: int = 0) -> str:
    payload = {"sub": user_id, "email": email, "tv": token_version, "exp": datetime.now(timezone.utc) + timedelta(hours=1), "type": "access"}
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_mfa_token(user_id: str, email: str) -> str:
    """Short-lived proof that the password step succeeded. It is not usable as an access or refresh token."""
    payload = {"sub": user_id, "email": email, "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "type": "mfa"}
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def platform_mfa_required() -> bool:
    return os.environ.get("REQUIRE_PLATFORM_MFA", "false").strip().lower() == "true"

def create_refresh_token(user_id: str, token_version: int = 0) -> str:
    payload = {"sub": user_id, "tv": token_version, "exp": datetime.now(timezone.utc) + timedelta(days=7), "type": "refresh"}
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def cookie_policy():
    frontend_url = os.environ.get("FRONTEND_URL", "")
    secure_cookie = os.environ.get("COOKIE_SECURE", "").lower() == "true" or frontend_url.startswith("https://")
    # "none" keeps a cross-site frontend (different domain than the API) working; a same-site or proxied frontend
    # should set COOKIE_SAMESITE=lax. Unsafe requests are additionally protected by the CSRF header check.
    same_site = (os.environ.get("COOKIE_SAMESITE") or ("none" if secure_cookie else "lax")).lower()
    if same_site == "none" and not secure_cookie:
        same_site = "lax"
    return secure_cookie, same_site

def tokens_in_body() -> bool:
    """Sessions live in httpOnly cookies. Tokens are only echoed in JSON for explicit non-browser integrations."""
    return os.environ.get("EXPOSE_TOKENS_IN_BODY", "false").strip().lower() == "true"

def session_body(payload: dict, access: str, refresh: Optional[str]) -> dict:
    if tokens_in_body():
        return {**payload, "access_token": access, "refresh_token": refresh}
    return payload

def set_auth_cookies(response: Response, access: str, refresh: Optional[str] = None):
    secure_cookie, same_site = cookie_policy()
    response.set_cookie("access_token", access, httponly=True, secure=secure_cookie, samesite=same_site, max_age=3600, path="/")
    if refresh:
        response.set_cookie("refresh_token", refresh, httponly=True, secure=secure_cookie, samesite=same_site, max_age=604800, path="/")

DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode("utf-8")

def self_registration_enabled() -> bool:
    flag = os.environ.get("ALLOW_SELF_REGISTRATION")
    if flag is not None and flag.strip() != "":
        return flag.strip().lower() == "true"
    return not PRODUCTION_CONFIG_STATUS.get("production", False)

def client_ip_for(request: Request) -> str:
    default_trust = "true" if PRODUCTION_CONFIG_STATUS.get("production") else "false"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and os.environ.get("TRUST_PROXY_HEADERS", default_trust).lower() == "true":
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if user.get("status", "active") != "active":
            raise HTTPException(status_code=403, detail="User account is disabled")
        if user.get('pos_creation_pending'):
            raise HTTPException(403, {'code': 'ACCOUNT_PROVISIONING_PENDING', 'message': 'This account is waiting for POS provisioning to finish. Ask your administrator to review its sync status.'})
        if payload.get("tv", 0) != user.get("token_version", 0):
            raise HTTPException(status_code=401, detail="Session revoked")
        path = getattr(getattr(request, "url", None), "path", "")
        if platform_mfa_required() and is_platform_staff(user) and not path.startswith("/api/auth/"):
            if not await db.user_mfa.find_one({"user_id": user["id"], "enabled": True}, {"_id": 1}):
                raise HTTPException(status_code=403, detail={"code": "MFA_ENROLLMENT_REQUIRED", "message": "Set up two-factor authentication to continue."})
        return user
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def validate_business_access(user: dict, business_id: Optional[str]) -> Optional[dict]:
    if not business_id:
        return None
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    if not is_platform_staff(user):
        if business_id not in user.get("business_ids", []):
            raise HTTPException(status_code=403, detail="Business access denied")
        if business.get("status") == "suspended":
            raise HTTPException(status_code=403, detail={"code": "BUSINESS_SUSPENDED", "message": "This business is suspended. Contact Taskoora support."})
        if pos_membership(user, business_id).get("status") in {"inactive", "disabled"}:
            raise HTTPException(403, "This business membership is inactive")
    return business


async def require_business_management(user, business_id, *, owner_only=False, target_role=None):
    business = await validate_business_access(user, business_id)
    if is_platform_staff(user):
        return
    role = management_role(user, business_id, local_owner=(business or {}).get("owner_id") == user.get("id"))
    if owner_only and role != "business_owner":
        raise HTTPException(403, "Business owner authority is required in this business")
    if target_role and not can_assign_role({"role": role}, target_role):
        raise HTTPException(403, "Your role in this business cannot grant the requested role")

async def require_business_admin_scope(user, business_id, *, permission, owner_only=False):
    """Control-plane authority must be checked in the selected membership."""
    business = await validate_business_access(user, business_id)
    if is_platform_staff(user):
        require_platform_permission(user, permission)
    else:
        await require_business_management(user, business_id, owner_only=owner_only)
        tenant_read_scope(user, business_id, "settings",
                          local_owner=(business or {}).get("owner_id") == user.get("id"),
                          connected=bool((business or {}).get("pos_external_id")))
    return business

def require_record_owner_candidate(user, permission):
    """Reject callers without any owner authority before looking up an opaque ID."""
    if is_platform_staff(user):
        require_platform_permission(user, permission)
        return
    for target in user.get("business_ids", []):
        member = pos_membership(user, target)
        if member.get("role") in {"business_owner", "owner"} and member.get("status", "active") not in {"inactive", "disabled"}:
            return
    raise HTTPException(403, "Business owner authority is required")

def require_platform_permission(user: dict, permission: str):
    """Platform-level actions are authorised by permission, never by tenant role."""
    if not has_platform_permission(user, permission):
        raise HTTPException(status_code=403, detail="Insufficient platform permissions")

@api_router.get('/operations/health')
async def operations_health(request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, 'audit.read')
    queues = await queue_health(db)
    bridge = {'configured': bool(POS_CORE_API_BASE_URL), 'healthy': False}
    if POS_CORE_API_BASE_URL:
        try:
            result = await pos_core_session_request('GET', 'admincore/capabilities', use_login=False)
            capabilities = result.get('data', result)
            bridge.update(capabilities=capabilities, healthy=all(capabilities.get(key) == 1 for key in
                          ['product_commands', 'outlet_commands', 'privacy_erasures', 'frozen_exports']))
        except Exception:
            bridge['error'] = 'Authenticated POS bridge check failed; inspect server logs and paired configuration'
    healthy = queues['healthy'] and bridge['healthy']
    return JSONResponse({**queues, 'healthy': healthy, 'bridge': bridge, 'checked_at': datetime.now(timezone.utc).isoformat()},
                        status_code=200 if healthy else 503, headers={'Cache-Control': 'private, no-store'})

def require_roles(user: dict, *roles: str):
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

def require_reason(reason: Optional[str], minimum: int = 5) -> str:
    reason = (reason or "").strip()
    if len(reason) < minimum:
        raise HTTPException(status_code=400, detail=f"A reason of at least {minimum} characters is required for this action")
    return reason

async def validate_business_access_many(user: dict, business_ids: list[str]):
    for business_id in list(dict.fromkeys(business_ids or [])):
        await validate_business_access(user, business_id)

def pos_error_detail(exc: Exception):
    if isinstance(exc, HTTPException):
        return exc.detail
    return str(exc)

def summarize_response_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if "<html" in raw.lower() or "<!doctype" in raw.lower():
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else "HTML error page"
        body = re.sub(r"<style.*?</style>|<script.*?</script>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
        body = re.sub(r"<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", body).strip()
        return f"{title}: {body[:240]}"
    return raw[:300]

def compact_bridge_error_detail(detail) -> dict:
    if not isinstance(detail, dict):
        return {"message": str(detail)}
    compact = {
        key: value
        for key, value in detail.items()
        if key in ["code", "resource", "endpoint", "status_code", "url", "context", "message", "tried", "retry_after_seconds"]
    }
    response = detail.get("response")
    if response is not None:
        compact["response"] = summarize_response_text(response) if isinstance(response, str) else response
    return compact

def requests_error_detail(exc: requests.RequestException, context: str) -> dict:
    response = getattr(exc, "response", None)
    detail = {
        "code": "POS_CORE_REQUEST_FAILED",
        "message": str(exc),
        "context": context,
    }
    if response is not None:
        detail.update({
            "status_code": response.status_code,
            "url": response.url,
        })
        try:
            detail["response"] = response.json()
        except ValueError:
            detail["response"] = summarize_response_text(response.text)
    return detail

def pos_rate_limit_seconds_remaining() -> int:
    return max(0, math.ceil(POS_CORE_RATE_LIMIT_UNTIL - time.time()))

def mark_pos_rate_limited(response=None) -> int:
    global POS_CORE_RATE_LIMIT_UNTIL
    cooldown = retry_after_seconds(response.headers.get("Retry-After") if response is not None else None,
                                  time.time(), POS_CORE_RATE_LIMIT_COOLDOWN_SECONDS)
    POS_CORE_RATE_LIMIT_UNTIL = max(POS_CORE_RATE_LIMIT_UNTIL, time.time() + cooldown)
    return pos_rate_limit_seconds_remaining()

def send_pos_request(session, method, url, **kwargs):
    global pos_next_request_at
    # All bridge callers, including pagination and login, share the same gate.
    with pos_request_lock:
        raise_if_pos_rate_limited(url)
        delay = pos_next_request_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        try:
            response = session.request(method, url, **kwargs)
            if response.status_code == 429:
                mark_pos_rate_limited(response)
                raise_if_pos_rate_limited(url)
            return response
        finally:
            pos_next_request_at = time.monotonic() + POS_CORE_REQUEST_INTERVAL_SECONDS

def raise_if_pos_rate_limited(resource: str):
    remaining = pos_rate_limit_seconds_remaining()
    if remaining > 0:
        raise HTTPException(status_code=429, detail={
            "code": "POS_RATE_LIMITED",
            "resource": resource,
            "retry_after_seconds": remaining,
            "message": f"POS is rate limiting bridge requests. AdminCore paused POS sync for {remaining} seconds to avoid making it worse.",
        })

def pos_core_login(session: requests.Session, headers: dict, email: Optional[str] = None, password: Optional[str] = None):
    raise_if_pos_rate_limited("auth/login")
    login_email = (email or POS_CORE_OWNER_EMAIL or "").strip().lower()
    login_password = password or POS_CORE_OWNER_PASSWORD
    if not login_email or not login_password:
        return
    login_url = f"{POS_CORE_API_BASE_URL}/api/auth/login"
    last_exc = None
    for attempt in range(3):
        try:
            response = send_pos_request(session, "POST",
                login_url,
                json={"email": login_email, "password": login_password},
                headers=headers,
                timeout=POS_CORE_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 429:
                cooldown = mark_pos_rate_limited(response)
                raise HTTPException(status_code=429, detail={
                    "code": "POS_RATE_LIMITED",
                    "resource": "auth/login",
                    "retry_after_seconds": cooldown,
                    "message": f"POS login is rate limited. AdminCore paused POS calls for {cooldown} seconds.",
                })
            response.raise_for_status()
            return
        except HTTPException:
            raise
        except requests.RequestException as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status not in [502, 503, 504] or attempt == 2:
                break
            time.sleep(2 * (attempt + 1))
    raise last_exc

async def mark_business_pos_status(business_id: str, status: str, error=None, extra: Optional[dict] = None):
    update = {
        "pos_provisioning_status": status,
        "pos_synced": status == "synced",
        "pos_last_provision_attempt_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if status == "synced":
        update.update({"pos_synced": True, "pos_provisioning_error": "", "pos_provisioning_error_detail": None})
    elif error is not None:
        detail = compact_bridge_error_detail(error) if isinstance(error, dict) else error
        update.update({
            "pos_synced": False,
            "pos_provisioning_error": bridge_error_message(detail),
            "pos_provisioning_error_detail": detail,
        })
    if extra:
        update.update(extra)
    await db.businesses.update_one({"id": business_id}, {"$set": update})

def sanitize_business_doc(business: Optional[dict]) -> Optional[dict]:
    if not business:
        return business
    sanitized = {k: v for k, v in business.items() if k not in ["_id", "pos_owner_password"]}
    sanitized["pos_owner_password_set"] = bool(business.get("pos_owner_password"))
    mapping_ready = bool(business.get("pos_external_id") and business.get("pos_tenant_id"))
    linked = bool(business.get("pos_external_id") or business.get("pos_tenant_id") or business.get("pos_synced") or business.get("pos_bridge_default"))
    sanitized["pos_connection_state"] = "connected" if mapping_ready else "setup_required" if linked else "not_configured"
    sanitized["operational_ready"] = mapping_ready and business.get("status") == "active" and (
        business.get("pos_provisioning_status") == "synced" if business.get("pos_provisioning_status")
        else bool(business.get("pos_synced"))
    )
    return sanitized

def sanitize_business_docs(businesses: list) -> list:
    return [sanitize_business_doc(business) for business in businesses]

async def rollback_failed_business_create(business_id: str, actor_id: str, owner_email: str = ""):
    cleanup_collections = [
        db.businesses,
        db.business_modules,
        db.settings,
        db.outlets,
        db.products,
        db.qr_codes,
        db.subscriptions,
        db.business_addons,
        db.feature_flags,
        db.integrations,
        db.pos_sales_orders,
        db.pos_bills,
        db.pos_payments,
        db.pos_tables,
        db.pos_reservations,
        db.pos_customers,
        db.pos_kitchen_kot,
        db.pos_inventory_admin,
        db.pos_inventory_movements,
        db.pos_staff_shifts,
        db.pos_reports_analytics,
    ]
    for collection in cleanup_collections:
        await collection.delete_many({"business_id": business_id})
    await db.businesses.delete_one({"id": business_id})
    # Audit records are evidence; a rolled-back business keeps its trail.
    await create_audit_log(business_id, actor_id, None, "rolled_back", "business", business_id, {"reason": "provisioning failed"})
    await db.users.update_one({"id": actor_id}, {"$pull": {"business_ids": business_id}})
    if owner_email:
        owner = await db.users.find_one({"email": owner_email}, {"_id": 0, "id": 1, "business_ids": 1, "role": 1})
        if owner:
            remaining = [bid for bid in owner.get("business_ids", []) if bid != business_id]
            if remaining:
                await db.users.update_one({"id": owner["id"]}, {"$set": {"business_ids": remaining, "updated_at": datetime.now(timezone.utc).isoformat()}})
            elif owner.get("role") == "business_owner":
                await db.users.delete_one({"id": owner["id"]})
            else:
                await db.users.update_one({"id": owner["id"]}, {"$set": {"business_ids": [], "updated_at": datetime.now(timezone.utc).isoformat()}})

def next_pos_provisioning_retry_at(attempts: int) -> str:
    delay = POS_PROVISIONING_RETRY_DELAYS_SECONDS[min(max(attempts - 1, 0), len(POS_PROVISIONING_RETRY_DELAYS_SECONDS) - 1)]
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()

async def queue_pos_provisioning_job(
    business_id: str,
    owner_name: Optional[str],
    owner_email: Optional[str],
    owner_password: Optional[str],
    actor: Optional[dict],
    run_after: Optional[str] = None,
) -> dict:
    now_ts = datetime.now(timezone.utc).isoformat()
    job = {
        "id": str(ObjectId()),
        "business_id": business_id,
        "owner_name": owner_name or "",
        "owner_email": (owner_email or "").strip().lower(),
        "owner_password": hash_pos_password(owner_password),
        "actor_id": (actor or {}).get("id", "system"),
        "actor_email": (actor or {}).get("email", "system"),
        "status": "pending",
        "attempts": 0,
        "max_attempts": POS_PROVISIONING_MAX_ATTEMPTS,
        "last_error": "",
        "run_after": run_after or (datetime.now(timezone.utc) + timedelta(seconds=POS_PROVISIONING_INITIAL_DELAY_SECONDS)).isoformat(),
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    active = await db.pos_provisioning_jobs.find_one({"business_id": business_id, "status": {"$in": ["pending", "retrying", "running"]}}, {"id": 1})
    if active:
        raise HTTPException(status_code=409, detail="POS provisioning is already queued or running for this business")
    try:
        await db.pos_provisioning_jobs.find_one_and_update(
            {"_id": f"provision:{business_id}", "status": {"$nin": ["pending", "retrying", "running"]}},
            {"$set": job, "$unset": {"steps": "", "lease": "", "result": "", "finished_at": ""}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="POS provisioning is already queued or running for this business") from exc
    await mark_business_pos_status(
        business_id,
        "pending",
        extra={"pos_provisioning_job_id": job["id"], "pos_provisioning_error": ""},
    )
    return {k: v for k, v in job.items() if k not in ["_id", "owner_password"]}

async def run_pos_provisioning_job(job: dict):
    business_id = job.get("business_id")
    if not business_id:
        return
    attempts = int(job.get("attempts") or 0) + 1
    now_ts = datetime.now(timezone.utc).isoformat()
    actor = {
        "id": job.get("actor_id") or "system",
        "email": job.get("actor_email") or "system",
    }
    lease = secrets.token_hex(16)
    job_filter = {"id": job["id"], "lease": lease}
    claim = await db.pos_provisioning_jobs.update_one(
        {"id": job["id"], "status": {"$in": ["pending", "retrying"]}, "attempts": attempts - 1},
        {"$set": {"status": "running", "lease": lease, "attempts": attempts, "last_attempt_at": now_ts, "updated_at": now_ts}},
    )
    if not claim.modified_count:
        return
    async def checkpoint(step, value):
        saved = await db.pos_provisioning_jobs.update_one(job_filter, {"$set": {f"steps.{step}": value}})
        if not saved.matched_count:
            raise HTTPException(status_code=409, detail="Provisioning job lease was replaced")
    current_step = "business"
    async def progress(step):
        nonlocal current_step
        current_step = step
        timestamp = datetime.now(timezone.utc).isoformat()
        saved = await db.pos_provisioning_jobs.update_one(job_filter, {
            "$set": {"current_step": step, "updated_at": timestamp},
            "$push": {"history": {"$each": [{"step": step, "status": "running", "attempt": attempts, "at": timestamp}], "$slice": -100}},
        })
        if not saved.matched_count:
            raise HTTPException(status_code=409, detail="Provisioning job lease was replaced")
        await db.businesses.update_one({"id": business_id, "pos_provisioning_job_id": job["id"]},
                                      {"$set": {"pos_provisioning_step": step}})
    try:
        if attempts > int(job.get("max_attempts") or POS_PROVISIONING_MAX_ATTEMPTS):
            raise HTTPException(status_code=503, detail="Provisioning retry limit reached after worker restart")
        result = await asyncio.wait_for(provision_business_end_to_end(
            business_id,
            owner_name=job.get("owner_name"),
            owner_email=job.get("owner_email"),
            owner_password=job.get("owner_password"),
            actor=actor,
            enqueue_on_failure=False,
            steps=job.get("steps", {}),
            checkpoint=checkpoint,
            progress=progress,
        ), timeout=300)
        if not result.get("configured"):
            raise HTTPException(status_code=503, detail="POS bridge is not configured")
        await db.pos_provisioning_jobs.update_one(
            job_filter,
            {"$set": {"status": "synced", "current_step": "complete", "result": result, "owner_password": "", "finished_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()},
             "$push": {"history": {"$each": [{"step": "complete", "status": "synced", "attempt": attempts, "at": datetime.now(timezone.utc).isoformat()}], "$slice": -100}}},
        )
    except Exception as exc:
        detail = pos_error_detail(exc)
        cooldown = rate_limit_delay(detail)
        status = "failed" if not cooldown and attempts >= int(job.get("max_attempts") or POS_PROVISIONING_MAX_ATTEMPTS) else "retrying"
        update = {
            "status": status,
            "last_error": detail,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if status == "retrying":
            update["run_after"] = next_pos_provisioning_retry_at(attempts)
            if cooldown:
                update["attempts"] = attempts - 1
                update["run_after"] = max(update["run_after"], (datetime.now(timezone.utc) + timedelta(seconds=cooldown)).isoformat())
        else:
            update["owner_password"] = ""
            update["finished_at"] = datetime.now(timezone.utc).isoformat()
        saved = await db.pos_provisioning_jobs.update_one(job_filter, {"$set": update,
            "$push": {"history": {"$each": [{"step": current_step, "status": status, "attempt": attempts, "at": update["updated_at"]}], "$slice": -100}}})
        if saved.matched_count:
            await mark_business_pos_status(business_id, "failed" if status == "failed" else "pending", detail)

async def process_due_pos_provisioning_jobs(limit: int = 1):
    if pos_rate_limit_seconds_remaining() > 0:
        return
    now_ts = datetime.now(timezone.utc).isoformat()
    stale_running_before = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    await db.pos_provisioning_jobs.update_many(
        {"status": "running", "updated_at": {"$lte": stale_running_before}},
        {"$set": {"status": "retrying", "run_after": now_ts, "updated_at": now_ts}},
    )
    cursor = db.pos_provisioning_jobs.find(
        {
            "status": {"$in": ["pending", "retrying"]},
            "run_after": {"$lte": now_ts},
        },
        {"_id": 0},
    ).sort("created_at", 1).limit(limit)
    async for job in cursor:
        await run_pos_provisioning_job(job)

async def pos_provisioning_worker():
    while True:
        try:
            await process_due_pos_provisioning_jobs()
        except Exception as exc:
            logger.warning("POS provisioning worker failed: %s", exc)
        await asyncio.sleep(POS_PROVISIONING_WORKER_INTERVAL_SECONDS)

async def ensure_business_owner_user(
    business_id: str,
    owner_name: Optional[str],
    owner_email: Optional[str],
    owner_password: Optional[str],
) -> Optional[dict]:
    email = (owner_email or "").strip().lower()
    if not email:
        return await db.users.find_one({"business_ids": business_id, "role": "business_owner"}, {"_id": 0, "password_hash": 0})
    existing = await db.users.find_one({"email": email}, {"_id": 0, "password_hash": 0})
    now_ts = datetime.now(timezone.utc).isoformat()
    if existing:
        if existing.get("role") not in ["business_owner", "platform_admin"]:
            await db.users.update_one({"id": existing["id"]}, {"$set": {"role": "business_owner", "updated_at": now_ts}})
            existing["role"] = "business_owner"
        await db.users.update_one({"id": existing["id"]}, {"$addToSet": {"business_ids": business_id}, "$set": {"updated_at": now_ts}})
        existing["business_ids"] = list(dict.fromkeys([*(existing.get("business_ids") or []), business_id]))
        return existing
    if not owner_password:
        raise HTTPException(status_code=400, detail="Owner password is required to create the business owner login")
    if owner_password.startswith("pbkdf2$"):
        raise HTTPException(status_code=400, detail="Create the AdminCore owner before retrying POS provisioning")
    if len(owner_password) < 8:
        raise HTTPException(status_code=400, detail="Owner password must be at least 8 characters")
    doc = {
        "id": str(ObjectId()),
        "email": email,
        "password_hash": hash_password(owner_password),
        "name": (owner_name or email.split("@")[0]).strip(),
        "role": "business_owner",
        "business_ids": [business_id],
        "status": "active",
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    await db.users.insert_one(doc)
    return {k: v for k, v in doc.items() if k not in {"_id", "password_hash"}}

async def provision_business_end_to_end(
    business_id: str,
    owner_name: Optional[str] = None,
    owner_email: Optional[str] = None,
    owner_password: Optional[str] = None,
    actor: Optional[dict] = None,
    enqueue_on_failure: bool = True,
    steps: Optional[dict] = None,
    checkpoint=None,
    progress=None,
) -> dict:
    if not POS_CORE_API_BASE_URL:
        await mark_business_pos_status(business_id, "not_configured")
        return {"configured": False, "message": "POS bridge is not configured"}
    await mark_business_pos_status(business_id, "pending")
    steps = dict(steps or {})
    async def record_step(name, value):
        if checkpoint:
            await checkpoint(name, value)
        steps[name] = value
    async def report_step(name):
        if progress:
            await progress(name)
    try:
        provisioned = steps.get("business")
        if not provisioned:
            await report_step("business")
            provisioned = await provision_admin_business_to_pos(business_id)
            await record_step("business", provisioned)
        business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
        await report_step("owner")
        owner = await ensure_business_owner_user(
            business_id,
            owner_name,
            owner_email,
            owner_password,
        )
        if not owner:
            raise HTTPException(status_code=400, detail="Business owner email and password are required before POS provisioning can complete")
        if steps.get("owner_id") != owner["id"]:
            await push_admin_user_to_pos(owner, owner_password, allow_generated_password=False, target_business_id=business_id)
            await record_step("owner_id", owner["id"])
        await report_step("outlet")
        outlet = await ensure_default_outlet_for_business(
            business_id,
            user=actor,
            sync_to_pos=True,
            pos_business_id=(business or {}).get("pos_external_id") or provisioned.get("business_id"),
            pos_tenant_id=(business or {}).get("pos_tenant_id") or provisioned.get("tenant_id"),
        )
        if not outlet or not outlet.get("pos_external_id") or not outlet.get("pos_synced"):
            raise HTTPException(status_code=502, detail="POS default outlet identity was not verified")
        await report_step("verification")
        verified_owner = await db.users.find_one({"id": owner["id"]}, {"_id": 0})
        owner_link = ((verified_owner or {}).get("pos_links") or {}).get(business_id, {})
        if (not verified_owner or not owner_link.get("user_id")
                or owner_link.get("business_id") != provisioned.get("business_id")
                or owner_link.get("tenant_id") != provisioned.get("tenant_id")):
            raise HTTPException(status_code=502, detail="POS owner identity was not verified")
        await mark_business_pos_status(
            business_id,
            "synced",
            extra={
                "pos_synced_at": datetime.now(timezone.utc).isoformat(),
                "pos_provisioning_step": "complete",
                "pos_owner_email": owner.get("email", ""),
                "pos_owner_id": owner_link["user_id"],
                "pos_default_outlet_id": outlet["pos_external_id"],
            },
        )
        return {"configured": True, "business": provisioned, "owner": {k: v for k, v in owner.items() if k != "password_hash"}, "outlet": outlet}
    except Exception as exc:
        detail = pos_error_detail(exc)
        if enqueue_on_failure:
            await queue_pos_provisioning_job(business_id, owner_name, owner_email, owner_password, actor, next_pos_provisioning_retry_at(1))
        await mark_business_pos_status(business_id, "failed", detail)
        if actor:
            await create_audit_log(business_id, actor["id"], actor["email"], "provision_failed", "business", business_id, {"error": detail})
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=502, detail=f"POS provisioning failed: {detail}") from exc

# ===================================================================
# AUDIT HELPER
# ===================================================================
async def create_audit_log(business_id, user_id, user_email, action, entity_type, entity_id=None, details=None, operation_id=None):
    document = {
        "id": f"operation:{business_id}:{operation_id}" if operation_id else str(ObjectId()),
        "business_id": business_id,
        "user_id": user_id,
        "user_email": user_email,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "details": details or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if operation_id:
        document["_id"] = document["id"]
        try:
            await db.audit_logs.update_one({"id": document["id"]}, {"$setOnInsert": document}, upsert=True)
        except DuplicateKeyError:
            if not await db.audit_logs.find_one({"_id": document["_id"], "id": document["id"]}, {"_id": 1}):
                raise
    else:
        await db.audit_logs.insert_one(document)


# ===================================================================
# PYDANTIC MODELS
# ===================================================================
class BaseModel(SchemaModel):
    model_config = ConfigDict(allow_inf_nan=False)


class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str

class BusinessCreate(BaseModel):
    name: str
    type: str
    plan: str = "starter"
    branding: dict = {}
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    owner_password: Optional[str] = None
    qr_organization_id: Optional[str] = None
    qr_setup_mode: str = "none"
    outlet_name: Optional[str] = None
    qr_code_type: str = "dynamic"
    qr_target_url: Optional[str] = None

class BusinessUpdate(BaseModel):
    expected_source_updated_at: Optional[str] = None
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    type: Optional[str] = None
    plan: Optional[str] = None
    status: Optional[str] = None
    branding: Optional[dict] = None
    qr_organization_id: Optional[str] = None
    reason: Optional[str] = None

class BusinessProvisionRequest(BaseModel):
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    owner_password: Optional[str] = None

class ClientCreate(BaseModel):
    owner_name: str
    email: str = ""
    phone: str = ""
    status: str = "active"
    business_ids: List[str] = []
    notes: str = ""

class ClientUpdate(BaseModel):
    owner_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    business_ids: Optional[List[str]] = None
    notes: Optional[str] = None

class OutletCreate(BaseModel):
    request_id: Optional[str] = None
    name: str
    code: Optional[str] = None
    address: str = ""
    manager_name: str = ""
    phone: str = ""
    status: str = "active"

class OutletUpdate(BaseModel):
    expected_updated_at: Optional[str] = Field(None, max_length=64)
    name: Optional[str] = None
    code: Optional[str] = None
    address: Optional[str] = None
    manager_name: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None

def make_outlet_code(prefix: str = "OUT") -> str:
    return f"{prefix}-{str(ObjectId())[-8:].upper()}"

def slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in str(value or "").lower()).strip("-").replace("--", "-")

async def unique_business_slug(preferred_slug: str, fallback_slug: Optional[str] = None, existing_id: Optional[str] = None) -> str:
    base_slug = slugify(preferred_slug) or slugify(fallback_slug) or "business"
    candidate = base_slug
    suffix = 2
    while True:
        existing = await db.businesses.find_one({"slug": candidate}, {"_id": 0, "id": 1})
        if not existing or (existing_id and existing.get("id") == existing_id):
            return candidate
        candidate = f"{base_slug}-{suffix}"
        suffix += 1

class ProductCreate(BaseModel):
    name: str
    price: float = Field(0, ge=0, le=9999999999.99)
    stock: int = Field(0, ge=0, le=2147483647)
    category: str = "General"
    business_id: Optional[str] = None
    outlet_id: Optional[str] = None
    active: bool = True
    source: str = "admin"
    external_id: Optional[str] = None
    request_id: Optional[str] = None

class ProductUpdate(BaseModel):
    expected_updated_at: Optional[str] = Field(None, max_length=64)
    name: Optional[str] = None
    price: Optional[float] = Field(None, ge=0, le=9999999999.99)
    stock: Optional[int] = Field(None, ge=0, le=2147483647)
    category: Optional[str] = None
    business_id: Optional[str] = None
    outlet_id: Optional[str] = None
    active: Optional[bool] = None
    source: Optional[str] = None
    external_id: Optional[str] = None


class ProductSourceLink(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=8, max_length=500)
    archive_duplicate: bool = False

class QRCodeCreate(BaseModel):
    name: str
    type: str = "dynamic"
    target_url: str
    business_id: Optional[str] = None
    outlet_id: Optional[str] = None
    qr_restaurant_id: Optional[str] = None
    description: str = ""
    status: str = "active"

class QRCodeUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    target_url: Optional[str] = None
    business_id: Optional[str] = None
    outlet_id: Optional[str] = None
    qr_restaurant_id: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None

class UserCreate(BaseModel):
    email: str
    name: str
    password: str
    role: str = "staff"
    business_ids: List[str] = []

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    business_ids: Optional[List[str]] = None

class FeatureFlagCreate(BaseModel):
    key: str
    name: str
    description: str = ""
    enabled: bool = False

class FeatureFlagUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None

class SettingUpdate(BaseModel):
    value: str

class ModuleToggle(BaseModel):
    enabled: bool
    config: dict = {}
    override_reason: str = ""

POS_RESOURCE_MODULES = {
    "sales-orders": "pos",
    "payments": "payments",
    "bills": "billing",
    "inventory": "inventory",
    "customers": "customers",
    "tables": "tables",
    "reservations": "reservations",
    "kitchen-kot": "kitchen",
    "reports-analytics": "analytics",
    "taxes-charges": "taxes_charges",
    "discounts-coupons": "discounts_coupons",
    "staff-shifts": "staff",
    "suppliers-purchasing": "suppliers_purchasing",
    "expenses": "expenses",
    "hardware-printers": "hardware_printers",
    "role-permissions": "users_roles",
    "notifications": "notifications",
    "import-export": "import_export",
    "integrations-webhooks": "integrations",
    "audit-security": "audit_security",
    "qr-ordering": "tables",
    "central-kitchen": "inventory",
}

CORE_FEATURE_MODULES = {
    "outlets": "businesses",
    "products": "products",
    "users": "users_roles",
    "settings": "modules",
    "feature_flags": "feature_flags",
    "audit_logs": "audit_security",
    "integrations": "integrations",
    "pos_bridge": "pos_bridge",
    "subscriptions": "subscriptions",
    "qr_codes": "qr_codes",
}

FEATURE_TO_MODULE = {
    "businesses.enabled": "businesses",
    "users_roles.enabled": "users_roles",
    "modules.enabled": "modules",
    "plans.enabled": "plans",
    "subscriptions.enabled": "subscriptions",
    "feature_flags.enabled": "feature_flags",
    "pos.basic": "pos",
    "payments.basic": "payments",
    "payments.refund_reports": "payments",
    "billing.basic": "billing",
    "products.basic": "products",
    "reports.basic": "analytics",
    "reports.standard": "analytics",
    "reports.advanced": "analytics",
    "reports.multi_outlet": "analytics",
    "reports.scheduled": "analytics",
    "taxes.basic": "taxes_charges",
    "taxes.expanded": "taxes_charges",
    "discounts.manual": "discounts_coupons",
    "discounts.coupons": "discounts_coupons",
    "discounts.advanced": "discounts_coupons",
    "hardware.receipt_printer": "hardware_printers",
    "hardware.kot_printer": "hardware_printers",
    "hardware.printer_routing": "hardware_printers",
    "inventory.basic": "inventory",
    "inventory.advanced": "inventory",
    "inventory.batch_tracking": "inventory",
    "inventory.recipe_consumption": "inventory",
    "inventory.multi_outlet": "inventory",
    "inventory.stock_transfer": "inventory",
    "crm.basic": "customers",
    "crm.advanced": "customers",
    "crm.segmentation": "customers",
    "crm.unified_cross_outlet": "customers",
    "tables.basic": "tables",
    "qr.basic": "qr_codes",
    "qr.dynamic": "qr_codes",
    "qr.ordering": "qr_codes",
    "qr.analytics": "qr_codes",
    "qr.custom_branding": "qr_codes",
    "qr.bulk_generation": "qr_codes",
    "kot.basic": "kitchen",
    "kot.advanced": "kitchen",
    "kot.printer_routing": "kitchen",
    "kot.multi_station": "kitchen",
    "kot.central_kitchen": "kitchen",
    "staff.attendance": "staff",
    "suppliers.basic": "suppliers_purchasing",
    "suppliers.advanced": "suppliers_purchasing",
    "expenses.basic": "expenses",
    "expenses.advanced": "expenses",
    "notifications.basic": "notifications",
    "notifications.advanced": "notifications",
    "import_export.basic": "import_export",
    "import_export.advanced": "import_export",
    "loyalty.basic": "loyalty",
    "loyalty.advanced": "loyalty",
    "loyalty.cross_outlet": "loyalty",
    "reservations.basic": "reservations",
    "reservations.advanced": "reservations",
    "payroll.basic": "payroll",
    "payroll.advanced": "payroll",
    "delivery.basic": "delivery",
    "delivery.advanced": "delivery",
    "integrations.api": "integrations",
    "integrations.webhooks": "integrations",
    "audit.basic": "audit_security",
    "audit.advanced": "audit_security",
    "audit.export": "audit_security",
    "franchise.enabled": "franchise",
    "central_kitchen.enabled": "franchise",
}

MODULE_BASE_FEATURE = {
    "pos": "pos.basic",
    "payments": "payments.basic",
    "billing": "billing.basic",
    "products": "products.basic",
    "analytics": "reports.basic",
    "taxes_charges": "taxes.basic",
    "discounts_coupons": "discounts.manual",
    "hardware_printers": "hardware.receipt_printer",
    "inventory": "inventory.basic",
    "customers": "crm.basic",
    "tables": "tables.basic",
    "qr_codes": "qr.basic",
    "kitchen": "kot.basic",
    "staff": "staff.attendance",
    "suppliers_purchasing": "suppliers.basic",
    "expenses": "expenses.basic",
    "notifications": "notifications.basic",
    "import_export": "import_export.basic",
    "loyalty": "loyalty.basic",
    "reservations": "reservations.basic",
    "payroll": "payroll.basic",
    "delivery": "delivery.basic",
    "integrations": "integrations.webhooks",
    "audit_security": "audit.basic",
    "franchise": "franchise.enabled",
    "users_roles": "users_roles.enabled",
    "modules": "modules.enabled",
    "subscriptions": "subscriptions.enabled",
    "feature_flags": "feature_flags.enabled",
}

class IntegrationCreate(BaseModel):
    slug: str
    name: str
    type: str
    config: dict = {}

class IntegrationUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    config: Optional[dict] = None

class PlanCreate(BaseModel):
    name: str
    slug: str
    description: str = ""
    trial_days: int = 14
    pricing: dict = {"monthly": 0, "yearly": 0, "currency": "USD"}
    limits: dict = {"max_outlets": 1, "max_users": 3, "max_modules": 3, "max_integrations": 0, "max_products": 500, "max_transactions_monthly": 1000}
    included_modules: List[str] = []
    features: dict = {}
    sort_order: int = 0

class PlanUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    trial_days: Optional[int] = None
    pricing: Optional[dict] = None
    limits: Optional[dict] = None
    included_modules: Optional[List[str]] = None
    features: Optional[dict] = None
    sort_order: Optional[int] = None

class SubscriptionCreate(BaseModel):
    business_id: str
    plan_id: str
    billing_cycle: str = "monthly"
    status: str = "trial"

class SubscriptionUpdate(BaseModel):
    plan_id: Optional[str] = None
    status: Optional[str] = None
    billing_cycle: Optional[str] = None
    current_period_end: Optional[str] = None
    trial_end: Optional[str] = None
    metadata: Optional[dict] = None
    reason: Optional[str] = None

class BusinessAddonCreate(BaseModel):
    business_id: str
    addon_id: str
    quantity: int = 1
    status: str = "active"

class EntitlementOverrideCreate(BaseModel):
    business_id: str
    feature_code: str
    enabled: bool = True
    reason: str = ""

class LimitOverrideCreate(BaseModel):
    business_id: str
    limit_code: str
    value: object
    reason: str = ""

class POSAdminRecord(BaseModel):
    title: str
    business_id: Optional[str] = None
    outlet_id: Optional[str] = None
    status: str = "active"
    category: Optional[str] = None
    owner_name: Optional[str] = None
    contact: Optional[str] = None
    amount: Optional[float] = None
    due_date: Optional[str] = None
    notes: str = ""
    payment_status: Optional[str] = None
    refund_status: Optional[str] = None
    payment_method: Optional[str] = None
    receipt_number: Optional[str] = None
    invoice_number: Optional[str] = None
    order_items: List[dict] = []
    movement_type: Optional[str] = None
    movement_quantity: Optional[float] = None
    reorder_level: Optional[float] = None
    stock_by_outlet: dict = {}
    phone: Optional[str] = None
    email: Optional[str] = None
    loyalty_points: Optional[int] = None
    order_history: List[dict] = []
    dining_area: Optional[str] = None
    table_status: Optional[str] = None
    table_qr_code: Optional[str] = None
    reservations: List[dict] = []
    ticket_items: List[dict] = []
    chef_name: Optional[str] = None
    item_statuses: dict = {}
    report_type: Optional[str] = None
    tax_rate: Optional[float] = None
    service_charge: Optional[float] = None
    packaging_charge: Optional[float] = None
    delivery_charge: Optional[float] = None
    tax_mode: Optional[str] = None
    coupon_code: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    applies_to: Optional[str] = None
    usage_limit: Optional[int] = None
    metadata: dict = {}

class POSAdminRecordUpdate(BaseModel):
    title: Optional[str] = None
    business_id: Optional[str] = None
    outlet_id: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    owner_name: Optional[str] = None
    contact: Optional[str] = None
    amount: Optional[float] = None
    due_date: Optional[str] = None
    notes: Optional[str] = None
    payment_status: Optional[str] = None
    refund_status: Optional[str] = None
    payment_method: Optional[str] = None
    receipt_number: Optional[str] = None
    invoice_number: Optional[str] = None
    order_items: Optional[List[dict]] = None
    movement_type: Optional[str] = None
    movement_quantity: Optional[float] = None
    reorder_level: Optional[float] = None
    stock_by_outlet: Optional[dict] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    loyalty_points: Optional[int] = None
    order_history: Optional[List[dict]] = None
    dining_area: Optional[str] = None
    table_status: Optional[str] = None
    table_qr_code: Optional[str] = None
    reservations: Optional[List[dict]] = None
    ticket_items: Optional[List[dict]] = None
    chef_name: Optional[str] = None
    item_statuses: Optional[dict] = None
    report_type: Optional[str] = None
    tax_rate: Optional[float] = None
    service_charge: Optional[float] = None
    packaging_charge: Optional[float] = None
    delivery_charge: Optional[float] = None
    tax_mode: Optional[str] = None
    coupon_code: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    applies_to: Optional[str] = None
    usage_limit: Optional[int] = None
    metadata: Optional[dict] = None


async def seed_business_defaults(business_id: str):
    existing_modules = await db.business_modules.count_documents({"business_id": business_id})
    if existing_modules == 0:
        modules = await db.modules.find({}, {"_id": 0}).to_list(100)
        for mod in modules:
            await ensure_business_module_row(business_id, mod["slug"])

    existing_settings = await db.settings.count_documents({"business_id": business_id})
    if existing_settings == 0:
        for s in [
            {"category": "general", "key": "timezone", "value": "UTC", "type": "select", "label": "Timezone", "description": "Business timezone"},
            {"category": "general", "key": "currency", "value": "USD", "type": "select", "label": "Currency", "description": "Default currency"},
            {"category": "general", "key": "language", "value": "en", "type": "select", "label": "Language", "description": "Default language"},
            {"category": "notifications", "key": "email_notifications", "value": "true", "type": "boolean", "label": "Email Notifications", "description": "Send email notifications"},
            {"category": "notifications", "key": "sms_notifications", "value": "false", "type": "boolean", "label": "SMS Notifications", "description": "Send SMS notifications"},
        ]:
            s["id"] = str(ObjectId())
            s["business_id"] = business_id
            s["created_at"] = datetime.now(timezone.utc).isoformat()
            await db.settings.insert_one(s)

async def ensure_business_module_row(business_id: str, module_slug: str) -> dict:
    row = await db.business_modules.find_one({"business_id": business_id, "module_slug": module_slug}, {"_id": 0})
    if row:
        return row
    module = await db.modules.find_one({"slug": module_slug}, {"_id": 0})
    if not module:
        raise HTTPException(status_code=404, detail=f"Module {module_slug} not found")
    row = {
        "id": str(ObjectId()),
        "business_id": business_id,
        "module_slug": module_slug,
        "enabled": module.get("is_core", False),
        "config": module.get("default_config", {}),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        return await db.business_modules.find_one_and_update({"business_id": business_id, "module_slug": module_slug}, {"$setOnInsert": row},
            upsert=True, return_document=ReturnDocument.AFTER, projection={"_id": 0})
    except DuplicateKeyError:
        # A simultaneous first request may have initialized the same module.
        pass
    existing = await db.business_modules.find_one({"business_id": business_id, "module_slug": module_slug}, {"_id": 0})
    if not existing:
        raise HTTPException(409, "Module settings changed during initialization; retry the request")
    return existing

async def require_business_module_enabled(business_id: Optional[str], module_slug: Optional[str]):
    if not business_id or not module_slug:
        return
    operational_modules = (set(POS_RESOURCE_MODULES.values()) - {"users_roles", "integrations", "audit_security"}) | {"businesses", "products", "qr_codes", "outlets", "reports"}
    if module_slug in operational_modules:
        business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
        if business and business.get("pos_provisioning_status") in {"pending", "failed", "not_configured"}:
            raise HTTPException(status_code=409, detail={
                "code": "POS_BUSINESS_NOT_READY", "business_id": business_id,
                "provisioning_status": business["pos_provisioning_status"],
                "message": "Business setup is incomplete. Check provisioning status and retry before using POS operations.",
            })
    base_feature = MODULE_BASE_FEATURE.get(module_slug)
    if base_feature:
        await require_feature(business_id, base_feature)
    row = await ensure_business_module_row(business_id, module_slug)
    if not row.get("enabled", False):
        module = await db.modules.find_one({"slug": module_slug}, {"_id": 0, "name": 1})
        label = module.get("name") if module else module_slug
        raise_entitlement_error("MODULE_DISABLED", module=module_slug, detail=f"{label} module is disabled for this business")

async def require_module_for_business_scope(user: dict, business_id: Optional[str], module_slug: Optional[str]):
    if not module_slug:
        return
    if business_id:
        await require_business_module_enabled(business_id, module_slug)
        return
    if is_platform_staff(user):
        return
    for allowed_business_id in user.get("business_ids", []):
        await require_business_module_enabled(allowed_business_id, module_slug)

def raise_entitlement_error(code: str, **payload):
    detail = {"code": code, "upgradeRequired": code in ["FEATURE_NOT_INCLUDED", "PLAN_LIMIT_REACHED"], **payload}
    raise HTTPException(status_code=403, detail=detail)

def limit_value_add(base, add):
    if base == "unlimited" or add == "unlimited":
        return "unlimited"
    if base is None:
        return add
    if add is None:
        return base
    return int(base) + int(add)

def limit_allows(value, current: int) -> bool:
    if value == "unlimited":
        return True
    if value is None:
        return False
    return current < int(value)

async def get_business_plan(business_id: str) -> tuple[Optional[dict], Optional[dict]]:
    sub = await db.subscriptions.find_one({"business_id": business_id}, {"_id": 0})
    plan = None
    if sub:
        plan = await db.plans.find_one({"id": sub.get("plan_id")}, {"_id": 0})
        if not plan and sub.get("plan_slug"):
            plan_slug = str(sub.get("plan_slug")).lower()
            plan = await db.plans.find_one({"$or": [{"slug": plan_slug}, {"code": plan_slug.upper()}]}, {"_id": 0})
        if plan and sub.get("plan_id") != plan.get("id"):
            await db.subscriptions.update_one(
                {"id": sub.get("id")},
                {"$set": {"plan_id": plan["id"], "plan_slug": plan["slug"], "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            sub = {**sub, "plan_id": plan["id"], "plan_slug": plan["slug"]}
    if not plan:
        business = await db.businesses.find_one({"id": business_id}, {"_id": 0, "plan": 1})
        plan_slug = str((business or {}).get("plan") or "free").lower()
        if plan_slug == "enterprise":
            plan_slug = "business"
        plan = await db.plans.find_one({"$or": [{"slug": plan_slug}, {"code": plan_slug.upper()}]}, {"_id": 0})
        if plan and not sub:
            sub = {
                "id": "",
                "business_id": business_id,
                "plan_id": plan["id"],
                "plan_slug": plan["slug"],
                "status": "active",
                "billing_cycle": "monthly",
            }
    return sub, plan

async def get_business_entitlements(business_id: str) -> dict:
    sub, plan = await get_business_plan(business_id)
    features = {}
    limits = {}
    modules_allowed = set()
    sources = {"plan": [], "addons": [], "overrides": []}
    subscription_active = bool(sub and sub.get("status") in ["trial", "trialing", "active", "renewal", "past_due"])

    if plan and subscription_active:
        plan_rows = await db.plan_entitlements.find({"plan_id": plan["id"], "enabled": True}, {"_id": 0}).to_list(500)
        if not plan_rows and plan.get("features"):
            plan_rows = [{"feature_code": key, "enabled": bool(value)} for key, value in plan.get("features", {}).items() if value]
        for row in plan_rows:
            code = row.get("feature_code")
            if code:
                features[code] = True
                module_slug = FEATURE_TO_MODULE.get(code)
                if module_slug:
                    modules_allowed.add(module_slug)
                sources["plan"].append(code)
        for module_slug in plan.get("included_modules", []):
            modules_allowed.add(module_slug)
            base_feature = MODULE_BASE_FEATURE.get(module_slug)
            if base_feature:
                features[base_feature] = True
        limit_rows = await db.plan_limits.find({"plan_id": plan["id"]}, {"_id": 0}).to_list(200)
        if limit_rows:
            limits = {row["limit_code"]: row.get("value") for row in limit_rows}
        else:
            limits = {str(k).replace("max_", "") + ".max": v for k, v in plan.get("limits", {}).items() if str(k).startswith("max_")}

    addon_rows = await db.business_addons.find({"business_id": business_id, "status": "active"}, {"_id": 0}).to_list(100)
    for purchased in addon_rows:
        addon_id = purchased.get("addon_id")
        ent_rows = await db.addon_entitlements.find({"addon_id": addon_id, "enabled": True}, {"_id": 0}).to_list(200)
        for row in ent_rows:
            code = row.get("feature_code")
            if code:
                features[code] = True
                module_slug = FEATURE_TO_MODULE.get(code)
                if module_slug:
                    modules_allowed.add(module_slug)
                sources["addons"].append(code)
        limit_rows = await db.addon_limits.find({"addon_id": addon_id}, {"_id": 0}).to_list(100)
        quantity = int(purchased.get("quantity") or 1)
        for row in limit_rows:
            code = row.get("limit_code")
            value = row.get("value")
            if isinstance(value, int):
                value = value * quantity
            limits[code] = limit_value_add(limits.get(code), value)

    override_rows = await db.business_entitlement_overrides.find({"business_id": business_id}, {"_id": 0}).to_list(200)
    for row in override_rows:
        code = row.get("feature_code")
        if not code:
            continue
        features[code] = bool(row.get("enabled", True))
        module_slug = FEATURE_TO_MODULE.get(code)
        if module_slug and row.get("enabled", True):
            modules_allowed.add(module_slug)
        sources["overrides"].append(code)
    limit_override_rows = await db.business_limit_overrides.find({"business_id": business_id}, {"_id": 0}).to_list(100)
    for row in limit_override_rows:
        limits[row["limit_code"]] = row.get("value")

    module_rows = await db.business_modules.find({"business_id": business_id}, {"_id": 0}).to_list(200)
    module_enabled = {row["module_slug"]: bool(row.get("enabled", False)) for row in module_rows}
    effective_modules = sorted([module for module in modules_allowed if module_enabled.get(module, False)])
    billing_policy = await db.businesses.find_one({"id": business_id}, {"fulfillment_modes": 1}) or {}
    return {
        "fulfillment_modes": billing_policy.get("fulfillment_modes"),
        "business_id": business_id,
        "subscription": sub,
        "plan": plan,
        "subscription_active": subscription_active,
        "features": features,
        "limits": limits,
        "modules_allowed": sorted(modules_allowed),
        "modules_enabled": sorted([key for key, value in module_enabled.items() if value]),
        "effective_modules": effective_modules,
        "module_overrides": {row["module_slug"]: bool(row.get("enabled"))
                             for row in module_rows if row.get("explicit_toggle")},
        "sources": sources,
    }

async def has_feature(business_id: str, feature_code: str) -> bool:
    entitlements = await get_business_entitlements(business_id)
    return bool(entitlements["features"].get(feature_code))

async def require_feature(business_id: Optional[str], feature_code: Optional[str]):
    if not business_id or not feature_code:
        return
    if not await has_feature(business_id, feature_code):
        _, plan = await get_business_plan(business_id)
        raise_entitlement_error("FEATURE_NOT_INCLUDED", feature=feature_code, currentPlan=(plan or {}).get("code") or (plan or {}).get("slug"))

async def get_limit(business_id: str, limit_code: str):
    entitlements = await get_business_entitlements(business_id)
    return entitlements["limits"].get(limit_code)

async def enforce_business_count_limit(user: dict):
    """A customer owner may run as many businesses as the most generous plan among their existing businesses allows.
    The first business is always allowed; Taskoora staff are not limited."""
    if is_platform_staff(user):
        return
    owned = [bid for bid in dict.fromkeys(user.get("business_ids", [])) if bid]
    if not owned:
        return
    allowances = [await get_limit(bid, "businesses.max") for bid in owned]
    if "unlimited" in allowances:
        return
    numeric = [int(value) for value in allowances if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit())]
    allowed = max(numeric) if numeric else 1
    if len(owned) >= allowed:
        raise_entitlement_error("PLAN_LIMIT_REACHED", limit="businesses.max", allowed=allowed, current=len(owned))

async def enforce_limit(business_id: str, limit_code: str, current_usage: int):
    allowed = await get_limit(business_id, limit_code)
    if not limit_allows(allowed, current_usage):
        raise_entitlement_error("PLAN_LIMIT_REACHED", limit=limit_code, allowed=allowed, current=current_usage)


# ===================================================================
# AUTH ROUTES
# ===================================================================
auth_router = APIRouter(prefix="/auth", tags=["auth"])

@auth_router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    email = req.email.lower().strip()
    client_ip = client_ip_for(request)
    try:
        wait = await lockout_seconds_remaining(db.login_attempts, email, client_ip)
        if wait > 0:
            raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.", headers={"Retry-After": str(wait)})
        user = await db.users.find_one({"email": email})
    except PyMongoError as exc:
        logger.error("Database unavailable during login: %s", exc)
        raise HTTPException(status_code=503, detail="Database is offline. Start MongoDB and try again.")
    # Always run a bcrypt comparison so unknown emails are not distinguishable by timing.
    password_ok = verify_password(req.password, user["password_hash"] if user else DUMMY_PASSWORD_HASH)
    if not user or not password_ok:
        try:
            await record_failed_attempt(db.login_attempts, email, client_ip)
            if user:
                await create_audit_log(None, user["id"], email, "login_failed", "auth", None, {"ip": client_ip})
        except PyMongoError as exc:
            logger.warning("Could not record failed login: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("status", "active") != "active":
        raise HTTPException(status_code=403, detail="User account is disabled")
    if user.get('pos_creation_pending'):
        raise HTTPException(403, {'code': 'ACCOUNT_PROVISIONING_PENDING', 'message': 'This account is waiting for POS provisioning to finish. Ask your administrator to review its sync status.'})
    if await db.user_mfa.find_one({"user_id": user["id"], "enabled": True}, {"_id": 1}):
        # Password alone never yields a session for an MFA-protected account. Throttle counters stay until the code is verified.
        return {"mfa_required": True, "mfa_token": create_mfa_token(user["id"], email)}
    try:
        await clear_failed_attempts(db.login_attempts, email, client_ip)
    except PyMongoError as exc:
        logger.warning("Could not clear failed logins: %s", exc)
    user_id = user["id"]
    token_version = user.get("token_version", 0)
    access = create_access_token(user_id, email, token_version)
    refresh = create_refresh_token(user_id, token_version)
    set_auth_cookies(response, access, refresh)
    try:
        await create_audit_log(None, user_id, email, "login", "auth")
    except PyMongoError as exc:
        logger.warning("Login succeeded but audit log write failed: %s", exc)
    user_payload = {"id": user_id, "email": user["email"], "name": user["name"], "role": user["role"], "business_ids": user.get("business_ids", []), "status": user.get("status", "active"),
                    "platform_permissions": platform_permissions_for(user), "mfa_enabled": False,
                    "mfa_enrollment_required": platform_mfa_required() and is_platform_staff(user)}
    return session_body({**user_payload, "user": user_payload}, access, refresh)

@auth_router.get("/config")
async def auth_config():
    """Non-sensitive switches the login screen needs before anyone is signed in."""
    return {"self_registration": self_registration_enabled(), "mfa_required_for_platform_staff": platform_mfa_required()}

@auth_router.post("/register")
async def register(req: RegisterRequest, response: Response):
    if not self_registration_enabled():
        raise HTTPException(status_code=403, detail="Self-registration is disabled. Ask a Taskoora administrator to create your account.")
    if len(req.password) < 10:
        raise HTTPException(status_code=400, detail="Password must be at least 10 characters")
    email = req.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    user_id = str(ObjectId())
    doc = {"id": user_id, "email": email, "password_hash": hash_password(req.password), "name": req.name, "role": "business_owner", "business_ids": [], "status": "active", "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.users.insert_one(doc)
    access = create_access_token(user_id, email)
    refresh = create_refresh_token(user_id)
    set_auth_cookies(response, access, refresh)
    user_payload = {"id": user_id, "email": email, "name": req.name, "role": "business_owner", "business_ids": [], "status": "active", "platform_permissions": [], "mfa_enabled": False, "mfa_enrollment_required": False}
    return session_body({**user_payload, "user": user_payload}, access, refresh)

@auth_router.get("/me")
async def get_me(request: Request):
    user = await get_current_user(request)
    user.pop("token_version", None)
    enrolled = bool(await db.user_mfa.find_one({"user_id": user["id"], "enabled": True}, {"_id": 1}))
    return {**user, "platform_permissions": platform_permissions_for(user), "mfa_enabled": enrolled,
            "mfa_enrollment_required": platform_mfa_required() and is_platform_staff(user) and not enrolled}

@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    # Bumping token_version invalidates every access/refresh token issued so far.
    token = request.cookies.get("access_token") or request.cookies.get("refresh_token")
    if not token and request.headers.get("Authorization", "").lower().startswith("bearer "):
        token = request.headers["Authorization"][7:]
    if token:
        try:
            payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_exp": False})
            await db.users.update_one({"id": payload.get("sub")}, [{"$set": {"token_version": {"$add": [{"$ifNull": ["$token_version", 0]}, 1]}}}])
        except (pyjwt.InvalidTokenError, PyMongoError) as exc:
            logger.warning("Logout could not revoke tokens: %s", exc)
    secure_cookie, same_site = cookie_policy()
    response.delete_cookie("access_token", path="/", secure=secure_cookie, httponly=True, samesite=same_site)
    response.delete_cookie("refresh_token", path="/", secure=secure_cookie, httponly=True, samesite=same_site)
    return {"message": "Logged out"}

@auth_router.post("/refresh")
async def refresh_token(request: Request, response: Response):
    token = request.cookies.get("refresh_token") or request.headers.get("X-Refresh-Token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if user.get('pos_creation_pending'):
            raise HTTPException(403, 'This account is awaiting POS provisioning')
        if user.get("status", "active") != "active":
            raise HTTPException(status_code=403, detail="User account is disabled")
        if payload.get("tv", 0) != user.get("token_version", 0):
            raise HTTPException(status_code=401, detail="Session revoked")
        access = create_access_token(user["id"], user["email"], user.get("token_version", 0))
        refresh = create_refresh_token(user["id"], user.get("token_version", 0))
        set_auth_cookies(response, access, refresh)
        return session_body({"message": "Token refreshed"}, access, refresh)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


# ===================================================================
# CLIENT ROUTES
# ===================================================================
client_router = APIRouter(prefix="/clients", tags=["clients"])

class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str

class MfaCode(BaseModel):
    code: str

class MfaDisable(BaseModel):
    password: str
    code: str

class MfaReset(BaseModel):
    reason: str

def _mfa_qr_data_uri(uri: str) -> Optional[str]:
    try:
        buffer = BytesIO()
        qrcode.make(uri).save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as exc:  # the manual key still works without the image
        logger.warning("Could not render MFA QR image: %s", exc)
        return None

async def _check_second_factor(user_id: str, code: str) -> bool:
    """Validate a TOTP code (with replay protection) or burn a single-use recovery code."""
    record = await db.user_mfa.find_one({"user_id": user_id, "enabled": True})
    if not record:
        return False
    if mfa_lib.is_totp_shaped(code):
        secret = mfa_lib.decrypt_secret(record.get("secret_enc", ""))
        step = mfa_lib.verify_totp(secret, code, last_step=record.get("last_step")) if secret else None
        if step is None:
            return False
        # Conditional update makes replay protection atomic across concurrent requests.
        claimed = await db.user_mfa.update_one(
            {"user_id": user_id, "$or": [{"last_step": {"$lt": step}}, {"last_step": None}, {"last_step": {"$exists": False}}]},
            {"$set": {"last_step": step}})
        return claimed.modified_count == 1
    remaining = mfa_lib.consume_recovery_code(record.get("recovery_hashes", []), code)
    if remaining is None:
        return False
    claimed = await db.user_mfa.update_one({"user_id": user_id, "recovery_hashes": record.get("recovery_hashes", [])}, {"$set": {"recovery_hashes": remaining}})
    return claimed.modified_count == 1

@auth_router.post("/mfa/verify")
async def mfa_verify(req: MfaVerifyRequest, request: Request, response: Response):
    try:
        payload = pyjwt.decode(req.mfa_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Sign-in expired. Enter your password again.")
    if payload.get("type") != "mfa":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user or user.get("status", "active") != "active" or user.get('pos_creation_pending'):
        raise HTTPException(status_code=401, detail="Invalid sign-in")
    email, client_ip = user["email"], client_ip_for(request)
    wait = await lockout_seconds_remaining(db.login_attempts, email, client_ip)
    if wait > 0:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.", headers={"Retry-After": str(wait)})
    if not await _check_second_factor(user["id"], req.code):
        await record_failed_attempt(db.login_attempts, email, client_ip)
        await create_audit_log(None, user["id"], email, "mfa_failed", "auth", None, {"ip": client_ip})
        raise HTTPException(status_code=401, detail="Invalid verification code")
    await clear_failed_attempts(db.login_attempts, email, client_ip)
    token_version = user.get("token_version", 0)
    access = create_access_token(user["id"], email, token_version)
    refresh = create_refresh_token(user["id"], token_version)
    set_auth_cookies(response, access, refresh)
    await create_audit_log(None, user["id"], email, "login", "auth", None, {"mfa": True})
    user_payload = {"id": user["id"], "email": email, "name": user["name"], "role": user["role"], "business_ids": user.get("business_ids", []), "status": user.get("status", "active"),
                    "platform_permissions": platform_permissions_for(user), "mfa_enabled": True, "mfa_enrollment_required": False}
    return session_body({**user_payload, "user": user_payload}, access, refresh)

@auth_router.post("/mfa/setup")
async def mfa_setup(request: Request):
    user = await get_current_user(request)
    if await db.user_mfa.find_one({"user_id": user["id"], "enabled": True}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="Two-factor authentication is already enabled. Disable it first to re-enroll.")
    secret = mfa_lib.generate_secret()
    await db.user_mfa.update_one({"user_id": user["id"]}, {"$set": {"pending_secret_enc": mfa_lib.encrypt_secret(secret), "enabled": False, "updated_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    uri = mfa_lib.provisioning_uri(secret, user["email"])
    return {"secret": secret, "otpauth_uri": uri, "qr_data_uri": _mfa_qr_data_uri(uri)}

@auth_router.post("/mfa/enable")
async def mfa_enable(req: MfaCode, request: Request):
    user = await get_current_user(request)
    record = await db.user_mfa.find_one({"user_id": user["id"]})
    secret = mfa_lib.decrypt_secret((record or {}).get("pending_secret_enc", "")) if record and not record.get("enabled") else None
    if not secret:
        raise HTTPException(status_code=409, detail="Start setup first")
    step = mfa_lib.verify_totp(secret, req.code)
    if step is None:
        raise HTTPException(status_code=400, detail="That code is not valid. Check your authenticator app clock and try again.")
    codes, hashes = mfa_lib.new_recovery_codes()
    await db.user_mfa.update_one({"user_id": user["id"]}, {
        "$set": {"enabled": True, "secret_enc": mfa_lib.encrypt_secret(secret), "last_step": step, "recovery_hashes": hashes, "enrolled_at": datetime.now(timezone.utc).isoformat()},
        "$unset": {"pending_secret_enc": ""}})
    await create_audit_log(None, user["id"], user["email"], "mfa_enabled", "auth", user["id"])
    return {"enabled": True, "recovery_codes": codes}

@auth_router.post("/mfa/disable")
async def mfa_disable(req: MfaDisable, request: Request):
    user = await get_current_user(request)
    if is_platform_staff(user) and platform_mfa_required():
        raise HTTPException(status_code=403, detail="Two-factor authentication is mandatory for platform staff")
    stored = await db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 1})
    if not stored or not verify_password(req.password, stored["password_hash"]) or not await _check_second_factor(user["id"], req.code):
        await record_failed_attempt(db.login_attempts, user["email"], client_ip_for(request))
        raise HTTPException(status_code=401, detail="Password or verification code is incorrect")
    await db.user_mfa.delete_one({"user_id": user["id"]})
    await create_audit_log(None, user["id"], user["email"], "mfa_disabled", "auth", user["id"])
    return {"enabled": False}

@auth_router.get("/mfa/status")
async def mfa_status(request: Request):
    user = await get_current_user(request)
    record = await db.user_mfa.find_one({"user_id": user["id"], "enabled": True}, {"_id": 0, "recovery_hashes": 1, "enrolled_at": 1})
    return {"enabled": bool(record), "enrolled_at": (record or {}).get("enrolled_at"), "recovery_codes_remaining": len((record or {}).get("recovery_hashes", [])), "required": platform_mfa_required() and is_platform_staff(user)}

CLIENT_STATUSES = ["active", "trial", "suspended", "inactive"]

async def hydrate_client_businesses(client_doc: dict) -> dict:
    business_ids = client_doc.get("business_ids") or []
    businesses = []
    if business_ids:
        businesses = await db.businesses.find({"id": {"$in": business_ids}}, {"_id": 0, "id": 1, "name": 1, "type": 1, "plan": 1, "status": 1}).to_list(100)
    return {
        **client_doc,
        "assigned_businesses": businesses,
        "assigned_business_count": len(businesses),
    }

async def validate_client_business_ids(user: dict, business_ids: list[str]):
    if not business_ids:
        return
    rows = await db.businesses.find({"id": {"$in": business_ids}}, {"_id": 0, "id": 1}).to_list(len(set(business_ids)))
    found = {row["id"] for row in rows}
    missing = [business_id for business_id in business_ids if business_id not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown business ids: {', '.join(missing)}")
    if not is_platform_staff(user):
        allowed = set(user.get("business_ids", []))
        denied = [business_id for business_id in business_ids if business_id not in allowed]
        if denied:
            raise HTTPException(status_code=403, detail="Business access denied")

@client_router.get("")
async def list_clients(request: Request, business_id: Optional[str] = Query(None), status: Optional[str] = Query(None), search: Optional[str] = Query(None)):
    user = await get_current_user(request)
    query = {}
    if business_id:
        await validate_client_business_ids(user, [business_id])
        query["business_ids"] = business_id
    elif not is_platform_staff(user):
        query["business_ids"] = {"$in": user.get("business_ids", [])}
    if status and status != "all":
        query["status"] = status
    if search:
        query["$or"] = [
            {"owner_name": {"$regex": re.escape(search), "$options": "i"}},
            {"email": {"$regex": re.escape(search), "$options": "i"}},
            {"phone": {"$regex": re.escape(search), "$options": "i"}},
        ]
    rows = await db.clients.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [await hydrate_client_businesses(row) for row in rows]

@client_router.post("")
async def create_client(data: ClientCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    status = (data.status or "active").lower()
    if status not in CLIENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(CLIENT_STATUSES)}")
    business_ids = list(dict.fromkeys(data.business_ids or []))
    await validate_client_business_ids(user, business_ids)
    email = (data.email or "").strip().lower()
    if email and await db.clients.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Client email already exists")
    now_ts = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(ObjectId()),
        "owner_name": data.owner_name.strip(),
        "email": email,
        "phone": data.phone.strip(),
        "status": status,
        "business_ids": business_ids,
        "notes": data.notes,
        "created_by": user["id"],
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    await db.clients.insert_one(doc)
    await create_audit_log(business_ids[0] if business_ids else None, user["id"], user["email"], "created", "client", doc["id"], {"owner_name": doc["owner_name"], "email": email})
    return await hydrate_client_businesses({k: v for k, v in doc.items() if k != "_id"})

@client_router.put("/{client_id}")
async def update_client(client_id: str, data: ClientUpdate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    existing = await db.clients.find_one({"id": client_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Client not found")
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    original_business_ids = await recover_business_assignments(db, existing, update_data, platform_admin=user["role"] == "platform_admin")
    await validate_client_business_ids(user, original_business_ids if user["role"] == "platform_admin" else existing.get("business_ids", []))
    if "email" in update_data:
        update_data["email"] = (update_data["email"] or "").strip().lower()
        if update_data["email"]:
            duplicate = await db.clients.find_one({"email": update_data["email"], "id": {"$ne": client_id}})
            if duplicate:
                raise HTTPException(status_code=400, detail="Client email already exists")
    if "status" in update_data:
        update_data["status"] = (update_data["status"] or "active").lower()
        if update_data["status"] not in CLIENT_STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(CLIENT_STATUSES)}")
    if "business_ids" in update_data:
        update_data["business_ids"] = list(dict.fromkeys(update_data["business_ids"] or []))
        await validate_client_business_ids(user, update_data["business_ids"])
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.clients.update_one({"id": client_id}, {"$set": update_data})
    await create_audit_log((update_data.get("business_ids") or existing.get("business_ids") or [None])[0], user["id"], user["email"], "updated", "client", client_id, update_data)
    updated = await db.clients.find_one({"id": client_id}, {"_id": 0})
    return await hydrate_client_businesses(updated)

@client_router.delete("/{client_id}")
async def delete_client(client_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    existing = await db.clients.find_one({"id": client_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Client not found")
    original_business_ids = await recover_business_assignments(db, existing, {}, platform_admin=user["role"] == "platform_admin")
    await validate_client_business_ids(user, original_business_ids if user["role"] == "platform_admin" else existing.get("business_ids", []))
    await db.clients.delete_one({"id": client_id})
    await create_audit_log((existing.get("business_ids") or [None])[0], user["id"], user["email"], "deleted", "client", client_id)
    return {"message": "Client deleted"}


# ===================================================================
# BUSINESS ROUTES
# ===================================================================
business_router = APIRouter(prefix="/businesses", tags=["businesses"])

@business_router.get("")
async def list_businesses(request: Request):
    user = await get_current_user(request)
    if is_platform_staff(user):
        businesses = await db.businesses.find({}, {"_id": 0}).to_list(100)
    else:
        businesses = await db.businesses.find({"id": {"$in": user.get("business_ids", [])}}, {"_id": 0}).to_list(100)
    for business in businesses:
        if business.get('pos_external_id') and business.get('pos_tenant_id'):
            await enqueue_pos_read_snapshot('businesses', business['id'])
    return sanitize_business_docs(businesses)

@business_router.post("")
async def create_business(data: BusinessCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] != "business_owner" and not has_platform_permission(user, "businesses.write"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # With no subscription, business.plan is what decides entitlements, so it is a commercial grant:
    # only staff who may manage subscriptions can start a business on anything but the default plan.
    if data.plan != "starter" and not has_platform_permission(user, "subscriptions.write"):
        raise HTTPException(status_code=403, detail="Plans are assigned by Taskoora. New businesses start on the starter plan.")
    await enforce_business_count_limit(user)
    owner_email = (data.owner_email or "").strip().lower()
    owner_name = (data.owner_name or "").strip()
    owner_password = data.owner_password or ""
    if POS_CORE_API_BASE_URL:
        if not owner_email:
            raise HTTPException(status_code=400, detail="Owner email is required so the business can login to POS")
        if not owner_password or len(owner_password) < 8:
            raise HTTPException(status_code=400, detail="Owner password must be at least 8 characters")
    qr_setup_mode = (data.qr_setup_mode or "none").lower()
    if qr_setup_mode not in ["none", "link", "local"]:
        raise HTTPException(status_code=400, detail="qr_setup_mode must be none, link, or local")
    if qr_setup_mode == "link" and not data.qr_organization_id:
        raise HTTPException(status_code=400, detail="qr_organization_id is required when linking an existing QR workspace")
    if qr_setup_mode == "local" and not data.qr_target_url:
        raise HTTPException(status_code=400, detail="qr_target_url is required for local QR setup")
    if qr_setup_mode == "local":
        validate_target_url(data.qr_target_url or "")
        validate_qr_code_type(data.qr_code_type)
    slug = await unique_business_slug(data.name)
    biz_id = str(ObjectId())
    now_ts = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": biz_id,
        "name": data.name,
        "slug": slug,
        "type": data.type,
        "plan": data.plan,
        "status": "active",
        "branding": data.branding,
        "owner_id": user["id"],
        "qr_organization_id": data.qr_organization_id or "",
        "qr_setup_mode": qr_setup_mode,
        "pos_provisioning_status": "pending" if POS_CORE_API_BASE_URL else "not_configured",
        "pos_provisioning_error": "",
        "pos_owner_email": owner_email,
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    await db.businesses.insert_one(doc)
    if not is_platform_staff(user):
        await db.users.update_one({"id": user["id"]}, {"$addToSet": {"business_ids": biz_id}})
    await seed_business_defaults(biz_id)
    await create_audit_log(biz_id, user["id"], user["email"], "created", "business", biz_id, {"name": data.name, "plan": data.plan})
    owner = await ensure_business_owner_user(
        biz_id,
        owner_name or data.name,
        owner_email,
        owner_password,
    )
    created_outlet = await ensure_default_outlet_for_business(biz_id, user=user, sync_to_pos=False)
    pos_job = None
    if POS_CORE_API_BASE_URL:
        pos_job = await queue_pos_provisioning_job(
            biz_id,
            owner_name=owner_name or data.name,
            owner_email=owner_email,
            owner_password=owner_password,
            actor=user,
        )
    created_qr_code = None
    if qr_setup_mode == "local":
        outlet_doc = created_outlet or await ensure_default_outlet_for_business(biz_id, user=user, sync_to_pos=bool(POS_CORE_API_BASE_URL))
        qr_doc = {
            "id": str(ObjectId()),
            "name": f"{outlet_doc['name']} QR Menu",
            "type": validate_qr_code_type(data.qr_code_type),
            "target_url": validate_target_url(data.qr_target_url or ""),
            "business_id": biz_id,
            "outlet_id": outlet_doc["id"],
            "qr_restaurant_id": "",
            "description": "Created during business setup",
            "status": "active",
            "token": secrets.token_urlsafe(12) if validate_qr_code_type(data.qr_code_type) == "dynamic" else "",
            "scan_count": 0,
            "last_scan_at": None,
            "created_by": user["id"],
            "created_at": now_ts,
            "updated_at": now_ts,
        }
        await db.qr_codes.insert_one(qr_doc)
        await create_audit_log(biz_id, user["id"], user["email"], "created", "qr_code", qr_doc["id"], {"name": qr_doc["name"], "type": qr_doc["type"]})
        created_qr_code = qr_code_response({k: v for k, v in qr_doc.items() if k != "_id"}, request)
    created_doc = await db.businesses.find_one({"id": biz_id}, {"_id": 0})
    return {
        **sanitize_business_doc(created_doc),
        "created_owner": owner,
        "created_outlet": created_outlet,
        "created_qr_code": created_qr_code,
        "pos_provisioned": False,
        "pos_provisioning_job": pos_job,
    }

@business_router.post("/{business_id}/provision-pos", status_code=202)
async def provision_business_to_pos_endpoint(business_id: str, data: BusinessProvisionRequest, request: Request):
    if not POS_CORE_API_BASE_URL:
        raise HTTPException(status_code=503, detail="POS bridge is not configured")
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await validate_business_access(user, business_id)
    owner_email = (data.owner_email or "").strip().lower()
    owner_password = data.owner_password or ""
    if POS_CORE_API_BASE_URL:
        if not owner_email:
            raise HTTPException(status_code=400, detail="Owner email is required so the business can login to POS")
        if not owner_password or len(owner_password) < 8:
            raise HTTPException(status_code=400, detail="Owner password must be at least 8 characters")
    await ensure_business_owner_user(
        business_id,
        data.owner_name,
        owner_email,
        owner_password,
    )
    await db.businesses.update_one(
        {"id": business_id},
        {"$set": {"pos_owner_email": owner_email, "updated_at": datetime.now(timezone.utc).isoformat()}, "$unset": {"pos_owner_password": ""}},
    )
    job = await queue_pos_provisioning_job(
        business_id,
        owner_name=data.owner_name,
        owner_email=owner_email,
        owner_password=owner_password,
        actor=user,
    )
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
    return {"message": "POS provisioning queued", "pos_provisioned": False,
            "pos_provisioning_job": job, "business": sanitize_business_doc(business)}

@business_router.get("/{business_id}")
async def get_business(business_id: str, request: Request):
    user = await get_current_user(request)
    return sanitize_business_doc(await validate_business_access(user, business_id))

BUSINESS_STATUSES = ["active", "suspended", "inactive"]

@business_router.put("/{business_id}")
async def update_business(business_id: str, data: BusinessUpdate, request: Request):
    user = await get_current_user(request)
    business = await require_business_admin_scope(user, business_id, permission="businesses.write", owner_only=True)
    update_data = {k: v for k, v in data.model_dump().items() if v is not None and k not in {"reason", "expected_source_updated_at"}}
    if 'name' in update_data:
        update_data['name'] = update_data['name'].strip()
        if not update_data['name']:
            raise HTTPException(400, 'Business name cannot be blank')
    # The UI echoes plan/status back on every edit; unchanged values are not a change request.
    for field in ("plan", "status"):
        if field in update_data and update_data[field] == business.get(field):
            update_data.pop(field)
    # Plan and lifecycle state are platform-controlled. A customer must never set their own.
    if "plan" in update_data and not has_platform_permission(user, "subscriptions.write"):
        raise HTTPException(status_code=403, detail="Plan changes are managed by Taskoora")
    reason = None
    if "status" in update_data:
        if not has_platform_permission(user, "businesses.suspend"):
            raise HTTPException(status_code=403, detail="Business status is managed by Taskoora")
        if update_data["status"] not in BUSINESS_STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(BUSINESS_STATUSES)}")
        reason = require_reason(data.reason)
        # Tell the customer application first: a state the POS never saw must not be recorded as applied.
        await push_business_lifecycle_to_pos(business, update_data["status"])
    if 'name' in update_data and update_data['name'] != business.get('name') and business.get('pos_external_id'):
        name = update_data['name'].strip()
        if not name or len(name) > 120:
            raise HTTPException(400, 'Business name must contain 1 to 120 characters')
        if not data.expected_source_updated_at:
            await enqueue_pos_read_snapshot('businesses', business_id)
            raise HTTPException(409, 'Refresh this business from POS before renaming it; its source version is required.')
        headers = await pos_headers_for_admin_business(business_id)
        result = await pos_core_session_request('PUT', f"admincore/business-profile/{business['pos_external_id']}",
            json={'tenant_id': business.get('pos_tenant_id'), 'shopName': name, 'expected_updated_at': data.expected_source_updated_at},
            extra_headers=headers, use_login=False)
        profile = result.get('data', result)
        update_data.update({'name': profile['shopName'], 'pos_profile': profile, 'pos_source_updated_at': profile['updated_at']})
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    target = {"id": business_id}
    if update_data.get('pos_source_updated_at'):
        target['$or'] = [{'pos_source_updated_at': business.get('pos_source_updated_at')}, {'pos_source_updated_at': update_data['pos_source_updated_at']}]
    result = await db.businesses.update_one(target, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="Business changed during this update. Refresh to see the latest confirmed state.")
    previous = {k: business.get(k) for k in update_data if k != "updated_at"}
    details = {"before": previous, "after": {k: v for k, v in update_data.items() if k != "updated_at"}}
    if reason:
        details["reason"] = reason
    await create_audit_log(business_id, user["id"], user["email"], "status_changed" if reason else "updated", "business", business_id, details)
    return sanitize_business_doc(await db.businesses.find_one({"id": business_id}, {"_id": 0}))

@business_router.delete("/{business_id}")
async def delete_business(business_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Only platform admins can delete businesses")
    snapshot = await db.businesses.find_one({"id": business_id}, {"_id": 0, "pos_owner_password": 0})
    if not snapshot:
        raise HTTPException(status_code=404, detail="Business not found")
    if snapshot.get("pos_external_id") or snapshot.get("pos_tenant_id") or snapshot.get("pos_synced"):
        raise HTTPException(status_code=409, detail="This business is linked to POS. Suspend it instead; deleting only its AdminCore mapping would leave POS data and users disconnected.")
    linked_user = await db.users.find_one({f"pos_links.{business_id}": {"$exists": True}}, {"_id": 1})
    if linked_user:
        raise HTTPException(status_code=409, detail="This business has linked POS users. Restore its POS mapping before deleting it.")
    pending_profile = await db.pos_profile_jobs.find_one({
        "status": {"$in": ["pending", "running", "retrying", "failed"]},
        "$or": [{"event.original_business_ids": business_id}, {"event.business_ids": business_id}],
    }, {"_id": 1})
    pending_provisioning = await db.pos_provisioning_jobs.find_one({"business_id": business_id, "status": {"$in": ["pending", "running", "retrying", "failed"]}}, {"_id": 1})
    if pending_profile or pending_provisioning:
        raise HTTPException(status_code=409, detail="Finish or reconcile this business's pending POS jobs before deleting it.")
    result = await db.businesses.delete_one({"id": business_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Business not found")
    await db.users.update_many({"business_ids": business_id}, {"$pull": {"business_ids": business_id}, "$inc": {"token_version": 1}})
    await db.clients.update_many({"business_ids": business_id}, {"$pull": {"business_ids": business_id}})
    await db.outlets.delete_many({"business_id": business_id})
    await db.business_modules.delete_many({"business_id": business_id})
    await db.feature_flags.delete_many({"business_id": business_id})
    await db.settings.delete_many({"business_id": business_id})
    await db.integrations.delete_many({"business_id": business_id})
    await create_audit_log(business_id, user["id"], user["email"], "deleted", "business", business_id, {"before": {k: snapshot.get(k) for k in ("name", "slug", "type", "plan", "status", "owner_id")}})
    return {"message": "Business deleted"}

FULFILLMENT_MODES = ("TABLE", "TOKEN", "PICKUP", "DELIVERY")


@business_router.get("/{business_id}/fulfillment-modes")
async def get_fulfillment_modes(business_id: str, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "businesses.read")
    business = await validate_business_access(user, business_id)
    modes = business.get("fulfillment_modes") or dict.fromkeys(FULFILLMENT_MODES, True)
    applied = (business.get("pos_entitlements_applied") or {}).get("fulfillment_modes")
    return {"modes": modes, "version": business.get("fulfillment_modes_version", 0),
            "sync_status": "confirmed" if applied == modes else "pending" if business.get("fulfillment_modes") is not None else "default",
            "connected": bool(business.get("pos_external_id") and business.get("pos_tenant_id"))}


@business_router.put("/{business_id}/fulfillment-modes", status_code=202)
async def update_fulfillment_modes(business_id: str, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "modules.write")
    business = await validate_business_access(user, business_id)
    if not business.get("pos_external_id") or not business.get("pos_tenant_id"):
        raise HTTPException(status_code=409, detail="Complete POS provisioning before changing billing modes")
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="Provide a valid billing mode policy")
    modes = body.get("modes") if isinstance(body, dict) else None
    version = body.get("version") if isinstance(body, dict) else None
    if not isinstance(modes, dict) or set(modes) != set(FULFILLMENT_MODES) or any(type(v) is not bool for v in modes.values()):
        raise HTTPException(status_code=422, detail="Provide a boolean for TABLE, TOKEN, PICKUP and DELIVERY")
    if type(version) is not int or version < 0:
        raise HTTPException(status_code=422, detail="Provide the current billing mode version")
    result = await db.businesses.update_one({"id": business_id, "$or": [
        {"fulfillment_modes_version": version}, *([{"fulfillment_modes_version": {"$exists": False}}] if version == 0 else [])
    ]}, {"$set": {"fulfillment_modes": modes}, "$inc": {"fulfillment_modes_version": 1}})
    if not result.matched_count:
        raise HTTPException(status_code=409, detail="Billing modes changed. Reload before saving again")
    await create_audit_log(business_id, user["id"], user.get("email"), "updated", "billing_modes", business_id,
                           {"before": business.get("fulfillment_modes"), "after": modes})
    await pos_sync_worker.enqueue_snapshot("tenant-entitlements", business_id, priority=100)
    return await get_fulfillment_modes(business_id, request)


@business_router.get("/{business_id}/entitlements")
async def get_entitlements(business_id: str, request: Request):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    entitlements = await get_business_entitlements(business_id)
    usage = {
        "outlets": await db.outlets.count_documents({"business_id": business_id, "status": "active"}),
        "users": await db.users.count_documents({"business_ids": business_id}),
        "modules": await db.business_modules.count_documents({"business_id": business_id, "enabled": True}),
        "integrations": await db.integrations.count_documents({"business_id": business_id}),
        "products": await db.products.count_documents({"business_id": business_id}),
        "qr_codes": await db.qr_codes.count_documents({"business_id": business_id}),
    }
    return {
        "plan": {"name": entitlements["plan"].get("name"), "slug": entitlements["plan"].get("slug"), "code": entitlements["plan"].get("code")} if entitlements.get("plan") else None,
        "subscription": entitlements.get("subscription"),
        "limits": entitlements.get("limits", {}),
        "usage": usage,
        "features": entitlements.get("features", {}),
        "modules_allowed": entitlements.get("modules_allowed", []),
        "modules_enabled": entitlements.get("modules_enabled", []),
        "effective_modules": entitlements.get("effective_modules", []),
        "sources": entitlements.get("sources", {}),
    }


# ===================================================================
# OUTLET ROUTES
# ===================================================================
outlet_router = APIRouter(prefix="/outlets", tags=["outlets"])

def outlet_sync_error(exc: HTTPException, operation: str) -> HTTPException:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    logger.warning("POS outlet %s failed: %s", operation, exc.detail)
    unauthorized = detail.get("status_code") in (401, 403)
    return HTTPException(status_code=502, detail={
        "code": "POS_BRIDGE_UNAUTHORIZED" if unauthorized else "POS_OUTLET_SYNC_FAILED",
        "message": ("AdminCore cannot authenticate with POS. Check the POS connection settings, then retry."
                    if unauthorized else f"Could not confirm the outlet {operation} with POS. Check the connection before retrying."),
    })

def require_source_edit_version(record, expected):
    if not POS_CORE_API_BASE_URL or not record.get("pos_external_id"):
        return
    current = (record.get("pos_metadata") or {}).get("updated_at")
    if not current:
        raise HTTPException(409, {"code": "POS_SOURCE_REFRESH_REQUIRED", "message": "Refresh this record from POS before editing it so its source version can be checked."})
    if not isinstance(expected, str) or expected != current:
        raise HTTPException(409, {"code": "POS_SOURCE_VERSION_CONFLICT", "message": "This record changed since it was loaded. Reload it before saving or deleting."})


@outlet_router.get("/business/{business_id}")
async def list_outlets(business_id: str, request: Request, response: Response = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), status: str = "all"):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    if status not in {"all", "active", "inactive", "archived"}:
        raise HTTPException(422, "Invalid outlet status filter")
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["outlets"])
    if await is_pos_connected_business(business_id):
        await cleanup_mismatched_pos_imports("outlets", business_id)
        job = await enqueue_pos_read_snapshot("outlets", business_id)
        if response is not None:
            response.headers["X-POS-Refresh-Status"] = job["status"]
    query = {"business_id": business_id, "deleted_at": None, "source_present": {"$ne": False}, "tenant_scope_status": {"$ne": "quarantined"}}
    if status != "all":
        query["status"] = status
    rows = await db.outlets.find(query, {"_id": 0}).sort([("created_at", 1), ("id", 1)]).skip(offset).to_list(limit)
    jobs = await db.pos_outlet_jobs.find({"_id": {"$in": [row["id"] for row in rows]}}, {"status": 1}).to_list(len(rows)) if rows else []
    states = {job["_id"]: job.get("status") for job in jobs}
    for row in rows:
        row["pos_command_status"] = states.get(row["id"])
    if response is not None:
        response.headers["X-Total-Count"] = str(await db.outlets.count_documents(query))
    return rows

@outlet_router.post("/business/{business_id}")
async def create_outlet(business_id: str, data: OutletCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await require_business_management(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["outlets"])
    request_hash = hashlib.sha256(json.dumps(data.model_dump(), sort_keys=True).encode()).hexdigest()
    outlet_id = str(ObjectId())
    if data.request_id:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", data.request_id):
            raise HTTPException(400, "Invalid request identifier")
        outlet_id = hashlib.sha256(f"{business_id}:{data.request_id}".encode()).hexdigest()[:24]
        previous = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
        if previous:
            if previous.get("deleted_at") or previous.get("create_request_hash") != request_hash:
                raise HTTPException(409, "This outlet create request was already used with different details or is archived")
            return Response(content=json.dumps(previous), media_type="application/json", status_code=202 if previous.get("pos_sync_pending") else 200)
    await enforce_limit(business_id, "outlets.max", await db.outlets.count_documents({"business_id": business_id, "status": "active"}))
    status = (data.status or "active").lower()
    if status not in ["active", "inactive"]:
        raise HTTPException(status_code=400, detail="status must be active or inactive")
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0, "pos_external_id": 1, "pos_tenant_id": 1})
    doc = {
        "id": outlet_id,
        "create_request_hash": request_hash,
        "business_id": business_id,
        "name": data.name,
        "code": (data.code or "").strip() or make_outlet_code("OUT"),
        "address": data.address,
        "manager_name": data.manager_name,
        "phone": data.phone,
        "status": status,
        "pos_business_id": (business or {}).get("pos_external_id") or business_id,
        "pos_tenant_id": (business or {}).get("pos_tenant_id") or f"admincore-{business_id}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if POS_CORE_API_BASE_URL:
        doc["pos_command"] = pos_outlet_commands.build_command(doc, "create", {}, user)
        doc["pos_sync_pending"] = True
    doc["_id"] = outlet_id
    try:
        await db.outlets.insert_one(doc)
    except DuplicateKeyError:
        previous = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
        if previous and previous.get("create_request_hash") == request_hash:
            return Response(content=json.dumps(previous), media_type="application/json", status_code=202 if previous.get("pos_sync_pending") else 200)
        raise HTTPException(409, "Outlet identity already exists") from None
    if POS_CORE_API_BASE_URL:
        await pos_outlet_commands.ensure_job(doc)
        doc.pop("_id", None)
        return Response(content=json.dumps(doc), media_type="application/json", status_code=202)
    await create_audit_log(business_id, user["id"], user["email"], "created", "outlet", doc["id"], {"name": data.name})
    return await db.outlets.find_one({"id": doc["id"]}, {"_id": 0})

@outlet_router.put("/{outlet_id}")
async def update_outlet(outlet_id: str, data: OutletUpdate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    outlet = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
    if not outlet or outlet.get("deleted_at"):
        raise HTTPException(status_code=404, detail="Outlet not found")
    await require_business_management(user, outlet.get("business_id"))
    await require_business_module_enabled(outlet.get("business_id"), CORE_FEATURE_MODULES["outlets"])
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    require_source_edit_version(outlet, update_data.pop("expected_updated_at", None))
    if "code" in update_data:
        update_data["code"] = (update_data["code"] or "").strip() or outlet.get("code") or make_outlet_code("OUT")
    if "status" in update_data:
        update_data["status"] = (update_data["status"] or "active").lower()
        if update_data["status"] not in ["active", "inactive"]:
            raise HTTPException(status_code=400, detail="status must be active or inactive")
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    if POS_CORE_API_BASE_URL:
        pending = await pos_outlet_commands.enqueue(outlet, "update", update_data, user)
        pending.pop("_id", None)
        return Response(content=json.dumps(pending), media_type="application/json", status_code=202)
    result = await db.outlets.update_one({"id": outlet_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Outlet not found")
    outlet = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
    await create_audit_log(outlet["business_id"], user["id"], user["email"], "updated", "outlet", outlet_id, update_data)
    return outlet

@outlet_router.delete("/{outlet_id}")
async def delete_outlet(outlet_id: str, request: Request, expected_updated_at: Optional[str] = Query(None, max_length=64)):
    user = await get_current_user(request)
    outlet = await db.outlets.find_one({"id": outlet_id})
    if not outlet or outlet.get("deleted_at"):
        raise HTTPException(status_code=404, detail="Outlet not found")
    require_roles(user, "platform_admin", "business_owner", "manager")
    await require_business_management(user, outlet.get("business_id"))
    await require_business_module_enabled(outlet.get("business_id"), CORE_FEATURE_MODULES["outlets"])
    require_source_edit_version(outlet, expected_updated_at)
    if POS_CORE_API_BASE_URL:
        if not outlet.get("pos_external_id") and await pos_outlet_commands.discard_rejected_create(outlet):
            await create_audit_log(outlet["business_id"], user["id"], user["email"], "discarded", "outlet", outlet_id, {"rejected_create": True})
            return {"message": "Rejected outlet draft archived; no POS outlet was created"}
        await pos_outlet_commands.enqueue(outlet, "delete", {}, user)
        return Response(content=json.dumps({"message": "Outlet deletion queued; historical POS records are retained", "delete_pending": True}), media_type="application/json", status_code=202)
    await db.outlets.delete_one({"id": outlet_id})
    await create_audit_log(outlet["business_id"], user["id"], user["email"], "deleted", "outlet", outlet_id)
    return {"message": "Outlet deleted"}


@outlet_router.post("/{outlet_id}/retry-pos", status_code=202)
async def retry_outlet_command(outlet_id: str, request: Request):
    user = await get_current_user(request)
    require_roles(user, "platform_admin", "business_owner", "manager")
    outlet = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
    if not outlet or outlet.get("deleted_at"):
        raise HTTPException(404, "Outlet not found")
    await require_business_management(user, outlet.get("business_id"))
    await require_business_module_enabled(outlet.get("business_id"), CORE_FEATURE_MODULES["outlets"])
    if (outlet.get("pos_command") or {}).get("version") != 1:
        raise HTTPException(409, "This older outlet update needs a verified source mapping before retrying")
    result = await db.pos_outlet_jobs.update_one({"_id": outlet_id, "status": "failed"}, {"$set": {
        "status": "pending", "attempts": 0, "run_after": datetime.now(timezone.utc).isoformat(), "last_error": None}})
    if result.matched_count != 1:
        raise HTTPException(409, "No failed outlet command is available to retry")
    return {"status": "pending"}


@outlet_router.post("/{outlet_id}/link-pos")
async def link_outlet_source(outlet_id: str, data: ProductSourceLink, request: Request):
    return await link_pos_record_source("outlets", outlet_id, data, await get_current_user(request))


# ===================================================================
# PRODUCT ROUTES
# ===================================================================
product_router = APIRouter(prefix="/products", tags=["products"])

@product_router.get("")
async def list_products(
    request: Request,
    business_id: Optional[str] = Query(None),
    outlet_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=500),
    offset: int = Query(0, ge=0),
    response: Response = None,
):
    user = await get_current_user(request)
    if business_id:
        await validate_business_access(user, business_id)
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["products"])
    query = await pos_read_filter(user, business_id, "products")
    query["deleted_at"] = None
    if outlet_id:
        query["outlet_id"] = outlet_id
    if q:
        query["name"] = {"$regex": re.escape(q), "$options": "i"}
    query["tenant_scope_status"] = {"$ne": "quarantined"}
    pos_connected = business_id and await is_pos_connected_business(business_id)
    if pos_connected:
        await cleanup_mismatched_pos_imports("products", business_id)
    if pos_connected:
        job = await enqueue_pos_read_snapshot("products", business_id)
        if response is not None:
            response.headers["X-POS-Refresh-Status"] = job["status"]
    limit = limit if isinstance(limit, int) else 500
    offset = offset if isinstance(offset, int) else 0
    cursor = db.products.find(query, {"_id": 0}).sort([("name", 1), ("id", 1)])
    if offset:
        cursor = cursor.skip(offset)
    products = await cursor.to_list(limit)
    jobs = await db.pos_product_jobs.find({"_id": {"$in": [product["id"] for product in products]}}, {"status": 1}).to_list(len(products)) if products else []
    states = {job["_id"]: job["status"] for job in jobs}
    for product in products:
        product["pos_command_status"] = "needs_reconciliation" if product.get("pos_sync_pending") and (product.get("pos_command") or {}).get("version") != 1 else states.get(product["id"])
    if response is not None:
        response.headers["X-Total-Count"] = str(await db.products.count_documents(query))
    return products

@product_router.post("")
async def create_product(data: ProductCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if not data.business_id:
        raise HTTPException(status_code=400, detail="business_id is required")
    await require_business_management(user, data.business_id)
    await require_business_module_enabled(data.business_id, CORE_FEATURE_MODULES["products"])
    await pos_read_filter(user, data.business_id, "products")
    request_hash = hashlib.sha256(json.dumps(data.model_dump(), sort_keys=True).encode()).hexdigest()
    product_id = str(ObjectId())
    if data.request_id:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", data.request_id):
            raise HTTPException(400, "Invalid request identifier")
        product_id = hashlib.sha256(f"{data.business_id}:{data.request_id}".encode()).hexdigest()[:24]
        previous = await db.products.find_one({"id": product_id}, {"_id": 0})
        if previous:
            if previous.get("deleted_at"):
                raise HTTPException(409, "This create request completed previously and its product is now archived")
            if previous.get("create_request_hash") != request_hash:
                raise HTTPException(409, "This create request was already used with different product details")
            return Response(content=json.dumps(previous), media_type="application/json", status_code=202 if previous.get("pos_sync_pending") else 200)
    await enforce_limit(data.business_id, "products.max", await db.products.count_documents({"business_id": data.business_id}))
    now_ts = datetime.now(timezone.utc).isoformat()
    doc = {
        **data.model_dump(),
        "id": product_id,
        "create_request_hash": request_hash,
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    if POS_CORE_API_BASE_URL:
        doc["pos_command"] = pos_product_commands.build_command(doc, "create", {}, user)
        doc["pos_sync_pending"] = True
    doc["_id"] = product_id
    if doc.get("external_id"):
        existing = await db.products.find_one({"source": doc.get("source"), "external_id": doc["external_id"]})
        if existing:
            raise HTTPException(status_code=400, detail="Product external_id already exists for this source")
    try:
        await db.products.insert_one(doc)
    except DuplicateKeyError:
        previous = await db.products.find_one({"id": product_id}, {"_id": 0})
        if previous and previous.get("create_request_hash") == request_hash:
            return Response(content=json.dumps(previous), media_type="application/json", status_code=202 if previous.get("pos_sync_pending") else 200)
        raise HTTPException(409, "Product identity already exists") from None
    if POS_CORE_API_BASE_URL:
        await pos_product_commands.ensure_job(doc)
        doc.pop("_id", None)
        return Response(content=json.dumps(doc), media_type="application/json", status_code=202)
    await create_audit_log(doc.get("business_id"), user["id"], user["email"], "created", "product", doc["id"], {"name": doc["name"]})
    return await db.products.find_one({"id": doc["id"]}, {"_id": 0})

@product_router.get("/{product_id}")
async def get_product(product_id: str, request: Request):
    user = await get_current_user(request)
    query = await pos_read_filter(user, None, "products")
    product = await db.products.find_one({"$and": [query, {"id": product_id}]}, {"_id": 0})
    if not product or product.get("deleted_at"):
        raise HTTPException(status_code=404, detail="Product not found")
    await validate_business_access(user, product.get("business_id"))
    await require_business_module_enabled(product.get("business_id"), CORE_FEATURE_MODULES["products"])
    await pos_read_filter(user, product.get("business_id"), "products")
    return product


@product_router.post("/{product_id}/retry-pos", status_code=202)
async def retry_product_command(product_id: str, request: Request):
    user = await get_current_user(request)
    require_roles(user, "platform_admin", "business_owner", "manager")
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(404, "Product not found")
    await require_business_management(user, product.get("business_id"))
    await pos_read_filter(user, product.get("business_id"), "products")
    if (product.get("pos_command") or {}).get("version") != 1:
        raise HTTPException(409, "This older update requires an explicit verified POS mapping before retrying")
    result = await db.pos_product_jobs.update_one({"_id": product_id, "status": "failed"}, {"$set": {"status": "pending", "attempts": 0, "run_after": datetime.now(timezone.utc).isoformat(), "last_error": None}})
    if not result.matched_count:
        raise HTTPException(409, "There is no failed product command to retry")
    return {"status": "pending"}


async def link_pos_record_source(resource, record_id, data, user):
    collection = db[resource]
    require_roles(user, "platform_admin")
    record = await collection.find_one({"id": record_id}, {"_id": 0})
    if not record:
        raise HTTPException(404, "Record not found")
    if record.get("deleted_at"):
        raise HTTPException(409, "Archived mappings require an explicit restoration review")
    if record.get("pos_sync_pending") and (record.get("pos_command") or {}).get("version") == 1:
        raise HTTPException(409, "Finish or resolve the pending POS command before changing its source mapping")
    business_id = record["business_id"]
    await validate_business_access(user, business_id)
    async with import_lease(db.pos_import_leases, business_id, resource):
        record = await collection.find_one({"id": record_id}, {"_id": 0})
        if not record or record.get("deleted_at") or (record.get("pos_sync_pending") and (record.get("pos_command") or {}).get("version") == 1):
            raise HTTPException(409, "The record changed before mapping review; reload it before continuing")
        rows = normalize_pos_bridge_rows(await pos_bridge_request(resource, {}, business_id=business_id))
        source = next((row for row in rows if str(row.get("id")) == data.source_id), None)
        if not source:
            raise HTTPException(404, "That record was not found in the verified POS business")
        await assert_pos_row_scope(resource, source, business_id)
        canonical = await collection.find_one({"business_id": business_id, "pos_external_id": data.source_id, "id": {"$ne": record_id}}, {"_id": 0})
        if canonical and (canonical.get("deleted_at") or (canonical.get("pos_sync_pending") and (canonical.get("pos_command") or {}).get("version") == 1)):
            raise HTTPException(409, "The source mapping is archived or has an unfinished command; resolve it before merging this duplicate")
        if canonical:
            if not data.archive_duplicate:
                raise HTTPException(409, {"code": "POS_SOURCE_ALREADY_LINKED", "canonical_id": canonical["id"], "message": "This source record already has an AdminCore record. Explicitly archive the local duplicate to retain that mapping."})
            changes = {"deleted_at": datetime.now(timezone.utc).isoformat(), "source_alias_to": canonical["id"], "pos_sync_pending": False}
        else:
            changes = {key: source[key] for key in (("name", "price", "stock", "category", "active") if resource == "products" else ("name", "code", "manager_name", "phone", "status")) if key in source}
            changes.update({"pos_external_id": data.source_id, "pos_business_id": pos_row_business_id(source),
                "pos_tenant_id": pos_row_tenant_id(source), "pos_metadata": source if resource == "products" else {key: source[key] for key in ("id", "updated_at", "name", "code", "status", "location", "manager_name", "phone") if key in source}, "pos_synced": True,
                "source_present": True, "pos_sync_pending": False, "pos_sync_error": None})
        if resource == "outlets" and not canonical:
            changes["address"] = source.get("address", source.get("location", ""))
        result = await collection.update_one({"id": record_id, "pos_command": record.get("pos_command"), "pos_sync_pending": record.get("pos_sync_pending")}, {"$set": changes})
        if result.matched_count != 1:
            raise HTTPException(409, "A new POS command was submitted during mapping review; the mapping was not changed")
        await create_audit_log(business_id, user["id"], user["email"], "source_mapping_reviewed", resource[:-1], record_id,
            {"reason": data.reason, "source_id": data.source_id, "canonical_id": (canonical or {}).get("id"), "archive_duplicate": data.archive_duplicate})
    return {"status": "linked", "canonical_id": (canonical or {}).get("id", record_id)}


@product_router.post("/{product_id}/link-pos")
async def link_product_source(product_id: str, data: ProductSourceLink, request: Request):
    return await link_pos_record_source("products", product_id, data, await get_current_user(request))

@product_router.put("/{product_id}")
async def update_product(product_id: str, data: ProductUpdate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await require_business_management(user, product.get("business_id"))
    await pos_read_filter(user, product.get("business_id"), "products")
    await require_business_module_enabled(product.get("business_id"), CORE_FEATURE_MODULES["products"])
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    require_source_edit_version(product, update_data.pop("expected_updated_at", None))
    if "business_id" in update_data:
        if update_data["business_id"] != product.get("business_id"):
            raise HTTPException(409, "Products cannot be moved between businesses. Create a separate product in the destination business.")
        await validate_business_access(user, update_data.get("business_id"))
        await require_business_module_enabled(update_data.get("business_id"), CORE_FEATURE_MODULES["products"])
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    if POS_CORE_API_BASE_URL:
        pending = await pos_product_commands.enqueue(product, "update", update_data, user)
        pending.pop("_id", None)
        return Response(content=json.dumps(pending), media_type="application/json", status_code=202)
    result = await db.products.update_one({"id": product_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    pos_push = None
    try:
        pos_push = await push_admin_product_to_pos(product)
    except HTTPException as exc:
        if POS_CORE_API_BASE_URL and product.get("business_id"):
            await db.products.update_one(
                {"id": product_id},
                {"$set": {
                    "pos_synced": False,
                    "pos_sync_pending": True,
                    "pos_sync_error": compact_bridge_error_detail(exc.detail),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
            product = await db.products.find_one({"id": product_id}, {"_id": 0})
    await create_audit_log(product.get("business_id"), user["id"], user["email"], "updated", "product", product_id, {**update_data, "pos_pushed": bool(pos_push)})
    return product

@product_router.delete("/{product_id}")
async def delete_product(product_id: str, request: Request, expected_updated_at: Optional[str] = Query(None, max_length=64)):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await require_business_management(user, product.get("business_id"))
    await pos_read_filter(user, product.get("business_id"), "products")
    await require_business_module_enabled(product.get("business_id"), CORE_FEATURE_MODULES["products"])
    require_source_edit_version(product, expected_updated_at)
    if POS_CORE_API_BASE_URL:
        await pos_product_commands.enqueue(product, "delete", {}, user)
        return Response(content=json.dumps({"message": "Deletion queued; the product remains unchanged until POS confirms it", "delete_pending": True}), media_type="application/json", status_code=202)
    try:
        await delete_admin_product_from_pos(product)
    except HTTPException as exc:
        if POS_CORE_API_BASE_URL and product.get("business_id"):
            await db.products.update_one(
                {"id": product_id},
                {"$set": {
                    "status": "inactive",
                    "active": False,
                    "pos_delete_pending": True,
                    "pos_sync_pending": True,
                    "pos_sync_error": compact_bridge_error_detail(exc.detail),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
            await create_audit_log(product.get("business_id"), user["id"], user["email"], "delete_pending", "product", product_id, {"pos_sync_error": compact_bridge_error_detail(exc.detail)})
            return {"message": "Product marked inactive; POS delete will retry when sync is available", "delete_pending": True}
    await db.products.delete_one({"id": product_id})
    await create_audit_log(product.get("business_id"), user["id"], user["email"], "deleted", "product", product_id)
    return {"message": "Product deleted"}


# ===================================================================
# QR CODE ROUTES
# ===================================================================
qr_code_router = APIRouter(prefix="/qr-codes", tags=["qr-codes"])

def public_api_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/api"

def qr_code_payload(doc: dict, request: Request) -> str:
    if doc.get("type") == "dynamic":
        token = doc.get("token")
        token_suffix = f"?token={token}" if token else ""
        return f"{public_api_base_url(request)}/qr-codes/{doc['id']}/scan{token_suffix}"
    return doc.get("target_url", "")

def qr_code_data_url(payload: str) -> str:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=3)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"

def qr_code_response(doc: dict, request: Request) -> dict:
    public_payload = qr_code_payload(doc, request)
    return {
        **doc,
        "payload_url": public_payload,
        "image_data_url": qr_code_data_url(public_payload),
    }

def validate_qr_code_type(value: str) -> str:
    normalized = (value or "dynamic").lower()
    if normalized not in ["static", "dynamic"]:
        raise HTTPException(status_code=400, detail="QR code type must be static or dynamic")
    return normalized

def validate_qr_status(value: str) -> str:
    normalized = (value or "active").lower()
    if normalized not in ["active", "inactive"]:
        raise HTTPException(status_code=400, detail="QR code status must be active or inactive")
    return normalized

def validate_target_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="target_url is required")
    if not value.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="target_url must start with http:// or https://")
    return value

async def validate_qr_code_ownership(business_id: str, outlet_id: str = ""):
    if not business_id:
        raise HTTPException(status_code=400, detail="business_id is required for QR codes")
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0, "id": 1})
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    if outlet_id:
        outlet = await db.outlets.find_one({"id": outlet_id}, {"_id": 0, "business_id": 1})
        if not outlet:
            raise HTTPException(status_code=404, detail="Outlet not found")
        if outlet.get("business_id") != business_id:
            raise HTTPException(status_code=400, detail="Outlet does not belong to the selected business")

@qr_code_router.get("")
async def list_qr_codes(
    request: Request,
    business_id: Optional[str] = Query(None),
    outlet_id: Optional[str] = Query(None),
    qr_restaurant_id: Optional[str] = Query(None),
):
    user = await get_current_user(request)
    if business_id:
        await validate_business_access(user, business_id)
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["qr_codes"])
    query = {}
    if business_id:
        query["business_id"] = business_id
    elif not is_platform_staff(user):
        query["business_id"] = {"$in": user.get("business_ids", [])}
    if outlet_id:
        query["outlet_id"] = outlet_id
    if qr_restaurant_id:
        query["qr_restaurant_id"] = qr_restaurant_id
    rows = await db.qr_codes.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [qr_code_response(row, request) for row in rows]

@qr_code_router.post("")
async def create_qr_code(data: QRCodeCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    qr_type = validate_qr_code_type(data.type)
    target_url = validate_target_url(data.target_url)
    status = validate_qr_status(data.status)
    await validate_qr_code_ownership(data.business_id or "", data.outlet_id or "")
    await validate_business_access(user, data.business_id)
    await require_business_module_enabled(data.business_id, CORE_FEATURE_MODULES["qr_codes"])
    if data.business_id and user["role"] != "platform_admin":
        await enforce_limit(data.business_id, "qr_codes.max", await db.qr_codes.count_documents({"business_id": data.business_id}))
    now_ts = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(ObjectId()),
        "name": data.name,
        "type": qr_type,
        "target_url": target_url,
        "business_id": data.business_id,
        "outlet_id": data.outlet_id or "",
        "qr_restaurant_id": data.qr_restaurant_id or "",
        "description": data.description,
        "status": status,
        "token": secrets.token_urlsafe(12) if qr_type == "dynamic" else "",
        "scan_count": 0,
        "last_scan_at": None,
        "created_by": user["id"],
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    await db.qr_codes.insert_one(doc)
    await create_audit_log(doc.get("business_id") or None, user["id"], user["email"], "created", "qr_code", doc["id"], {"name": doc["name"], "type": doc["type"]})
    return qr_code_response({k: v for k, v in doc.items() if k != "_id"}, request)

@qr_code_router.get("/{code_id}")
async def get_qr_code(code_id: str, request: Request):
    user = await get_current_user(request)
    doc = await db.qr_codes.find_one({"id": code_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="QR code not found")
    await validate_business_access(user, doc.get("business_id"))
    await require_business_module_enabled(doc.get("business_id"), CORE_FEATURE_MODULES["qr_codes"])
    return qr_code_response(doc, request)

@qr_code_router.put("/{code_id}")
async def update_qr_code(code_id: str, data: QRCodeUpdate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if "type" in update_data:
        update_data["type"] = validate_qr_code_type(update_data["type"])
    if "target_url" in update_data:
        update_data["target_url"] = validate_target_url(update_data["target_url"])
    if "status" in update_data:
        update_data["status"] = validate_qr_status(update_data["status"])
    existing = await db.qr_codes.find_one({"id": code_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="QR code not found")
    target_business_id = update_data.get("business_id", existing.get("business_id", ""))
    target_outlet_id = update_data.get("outlet_id", existing.get("outlet_id", ""))
    await validate_business_access(user, existing.get("business_id"))
    await validate_business_access(user, target_business_id)
    await validate_qr_code_ownership(target_business_id, target_outlet_id)
    await require_business_module_enabled(target_business_id, CORE_FEATURE_MODULES["qr_codes"])
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.qr_codes.update_one({"id": code_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="QR code not found")
    doc = await db.qr_codes.find_one({"id": code_id}, {"_id": 0})
    await create_audit_log(doc.get("business_id") or None, user["id"], user["email"], "updated", "qr_code", code_id, update_data)
    return qr_code_response(doc, request)

@qr_code_router.post("/{code_id}/regenerate")
async def regenerate_qr_code(code_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    doc = await db.qr_codes.find_one({"id": code_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="QR code not found")
    await validate_business_access(user, doc.get("business_id"))
    await require_business_module_enabled(doc.get("business_id"), CORE_FEATURE_MODULES["qr_codes"])
    if doc.get("type") != "dynamic":
        raise HTTPException(status_code=400, detail="Only dynamic QR links can be regenerated")
    update_data = {
        "token": secrets.token_urlsafe(12),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.qr_codes.update_one({"id": code_id}, {"$set": update_data})
    updated = await db.qr_codes.find_one({"id": code_id}, {"_id": 0})
    await create_audit_log(updated.get("business_id") or None, user["id"], user["email"], "regenerated", "qr_code", code_id)
    return qr_code_response(updated, request)

@qr_code_router.delete("/{code_id}")
async def delete_qr_code(code_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    doc = await db.qr_codes.find_one({"id": code_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="QR code not found")
    await validate_business_access(user, doc.get("business_id"))
    await require_business_module_enabled(doc.get("business_id"), CORE_FEATURE_MODULES["qr_codes"])
    await db.qr_codes.delete_one({"id": code_id})
    await create_audit_log(doc.get("business_id") or None, user["id"], user["email"], "deleted", "qr_code", code_id)
    return {"message": "QR code deleted"}

@qr_code_router.get("/{code_id}/scan")
async def scan_qr_code(code_id: str, token: Optional[str] = Query(None)):
    doc = await db.qr_codes.find_one({"id": code_id}, {"_id": 0})
    if not doc or doc.get("status") != "active":
        raise HTTPException(status_code=404, detail="QR code not found")
    if doc.get("token") and token != doc.get("token"):
        raise HTTPException(status_code=404, detail="QR code not found")
    now_ts = datetime.now(timezone.utc).isoformat()
    await db.qr_codes.update_one({"id": code_id}, {"$inc": {"scan_count": 1}, "$set": {"last_scan_at": now_ts}})
    return RedirectResponse(doc.get("target_url", "/"))


# ===================================================================
# MODULE ROUTES
# ===================================================================
module_router = APIRouter(prefix="/modules", tags=["modules"])

@module_router.get("")
async def list_modules(request: Request):
    await get_current_user(request)
    return await db.modules.find({}, {"_id": 0}).sort([("sort_order", 1), ("category", 1), ("name", 1)]).to_list(200)

@module_router.get("/business/{business_id}")
async def list_business_modules(business_id: str, request: Request):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    entitlements = await get_business_entitlements(business_id)
    allowed_modules = set(entitlements.get("modules_allowed", []))
    override_features = set(entitlements.get("sources", {}).get("overrides", []))
    biz_modules = await db.business_modules.find({"business_id": business_id}, {"_id": 0}).to_list(100)
    all_modules = await db.modules.find({}, {"_id": 0}).sort([("sort_order", 1), ("category", 1), ("name", 1)]).to_list(200)
    biz_module_map = {bm["module_slug"]: bm for bm in biz_modules}
    result = []
    for mod in all_modules:
        bm = biz_module_map.get(mod["slug"])
        if not bm:
            bm = await ensure_business_module_row(business_id, mod["slug"])
        base_feature = MODULE_BASE_FEATURE.get(mod["slug"])
        result.append({
            **mod,
            "enabled": bm.get("enabled", False),
            "config": bm.get("config", {}),
            "business_id": business_id,
            "included": mod["slug"] in allowed_modules,
            "outside_plan": mod["slug"] not in allowed_modules,
            "override": bool(base_feature and base_feature in override_features),
            "base_feature": base_feature,
        })
    return result

@module_router.put("/business/{business_id}/{module_slug}")
async def toggle_business_module(business_id: str, module_slug: str, data: ModuleToggle, request: Request):
    user = await get_current_user(request)
    if user["role"] != "business_owner" and not has_platform_permission(user, "modules.write"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await require_business_management(user, business_id, owner_only=True)
    base_feature = MODULE_BASE_FEATURE.get(module_slug)
    if data.enabled and base_feature and not await has_feature(business_id, base_feature):
        if user["role"] != "platform_admin":
            _, plan = await get_business_plan(business_id)
            raise_entitlement_error("FEATURE_NOT_INCLUDED", feature=base_feature, module=module_slug, currentPlan=(plan or {}).get("code") or (plan or {}).get("slug"))
        await db.business_entitlement_overrides.update_one(
            {"business_id": business_id, "feature_code": base_feature},
            {"$set": {"enabled": True, "reason": data.override_reason or "Platform admin module override", "updated_by": user["id"], "updated_at": datetime.now(timezone.utc).isoformat()}, "$setOnInsert": {"id": str(ObjectId()), "created_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        await create_audit_log(business_id, user["id"], user["email"], "overridden", "entitlement", base_feature, {"module": module_slug, "enabled": True})
    if data.enabled:
        existing_enabled = await db.business_modules.count_documents({"business_id": business_id, "enabled": True, "module_slug": {"$ne": module_slug}})
        if user["role"] != "platform_admin":
            await enforce_limit(business_id, "modules.max", existing_enabled)
        else:
            try:
                await enforce_limit(business_id, "modules.max", existing_enabled)
            except HTTPException:
                await db.business_limit_overrides.update_one(
                    {"business_id": business_id, "limit_code": "modules.max"},
                    {"$set": {"value": "unlimited", "reason": data.override_reason or "Platform admin module limit override", "updated_by": user["id"], "updated_at": datetime.now(timezone.utc).isoformat()}, "$setOnInsert": {"id": str(ObjectId()), "created_at": datetime.now(timezone.utc).isoformat()}},
                    upsert=True,
                )
    update = {"enabled": data.enabled, "explicit_toggle": True}
    if data.config:
        update["config"] = data.config
    await db.business_modules.update_one({"business_id": business_id, "module_slug": module_slug}, {"$set": update}, upsert=True)
    await create_audit_log(business_id, user["id"], user["email"], "toggled", "module", module_slug, {"enabled": data.enabled})
    return {"message": f"Module {module_slug} {'enabled' if data.enabled else 'disabled'}"}


# ===================================================================
# USER ROUTES
# ===================================================================
user_router = APIRouter(prefix="/users", tags=["users"])
USER_ROLES = list(ALL_ROLES)
# Changing any of these must end the account's existing sessions.
SESSION_REVOKING_FIELDS = ("password_hash", "email", "role", "status", "business_ids")
USER_STATUSES = ["active", "inactive"]

@user_router.get("")
async def list_users(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    if business_id:
        await validate_business_access(user, business_id)
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["users"])
    if is_platform_staff(user):
        require_platform_permission(user, "users.read")
        query = {"business_ids": business_id, "role": {"$ne": "platform_admin"}} if business_id else {}
        if user["role"] != "platform_admin":
            # Only super admins can see or manage other platform accounts.
            query["role"] = {"$nin": list(PLATFORM_ROLES)}
    else:
        bid = business_id or (user.get("business_ids", [""])[0] if user.get("business_ids") else "")
        if not bid:
            return []
        business = await validate_business_access(user, bid)
        tenant_read_scope(user, bid, "staff-shifts", local_owner=(business or {}).get("owner_id") == user.get("id"), connected=bool((business or {}).get("pos_external_id")))
        query = {"business_ids": bid, "role": {"$nin": list(PLATFORM_ROLES)}}
    users = await db.users.find(query, {"_id": 0, "password_hash": 0}).to_list(200)
    jobs = await db.pos_profile_jobs.find({"_id": {"$in": [row["id"] for row in users]}},
                                          {"_id": 1, "status": 1, "run_after": 1, "last_error": 1}).to_list(200)
    status_by_user = {job["_id"]: job for job in jobs}
    visible_ids = set(user.get('business_ids') or [])
    managed_businesses = {}
    if not is_platform_staff(user) and users:
        docs = await db.businesses.find({'id': {'$in': list(visible_ids)}}, {'_id': 0}).to_list(len(visible_ids))
        managed_businesses = {doc['id']: doc for doc in docs}
    for row in users:
        job = status_by_user.get(row["id"], {})
        row["pos_update_status"] = job.get("status")
        row["pos_update_retry_at"] = job.get("run_after")
        row["pos_update_error"] = bridge_error_message(job["last_error"])[:300] if job.get("last_error") else ""
        if is_platform_staff(user):
            row['can_manage_account'] = has_platform_permission(user, 'users.write') and (user['role'] == 'platform_admin' or row.get('role') not in PLATFORM_ROLES)
        else:
            targets = row.get('business_ids') or []
            can_manage = bool(targets)
            for target in targets:
                business = managed_businesses.get(target)
                try:
                    if not business or business.get('status') == 'suspended' or management_role(user, target, local_owner=business.get('owner_id') == user.get('id')) != 'business_owner':
                        can_manage = False
                except HTTPException:
                    can_manage = False
            row['can_manage_account'] = can_manage
            row['shared_account'] = bool(set(targets) - visible_ids)
            row['business_ids'] = [target for target in targets if target in visible_ids]
            row['pos_links'] = {target: link for target, link in (row.get('pos_links') or {}).items() if target in visible_ids}
            if not can_manage:
                row['pos_update_error'] = 'An account update needs attention from an administrator with access to every assignment.' if row['pos_update_error'] else ''
    return users

@user_router.post("")
async def create_user(data: UserCreate, request: Request, response: Response):
    user = await get_current_user(request)
    business_ids = list(dict.fromkeys(data.business_ids or []))
    if data.role in PLATFORM_ROLES and business_ids:
        raise HTTPException(400, 'Platform accounts cannot be assigned to customer businesses')
    if is_platform_staff(user):
        require_roles(user, 'platform_admin')
    elif not business_ids:
        raise HTTPException(403, 'Tenant users must belong to an authorized business')
    for business_id in business_ids:
        await require_business_management(user, business_id, target_role=data.role)
    email = data.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already exists")
    if data.role not in USER_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of: {', '.join(USER_ROLES)}")
    if is_platform_staff(user) and not can_assign_role(user, data.role):
        raise HTTPException(status_code=403, detail="You cannot assign a role at or above your own authority")
    if len(data.password or "") < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    business_ids = list(dict.fromkeys(data.business_ids or []))
    await validate_client_business_ids(user, business_ids)
    for business_id in business_ids:
        await require_business_management(user, business_id, target_role=data.role)
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
        await enforce_limit(business_id, "users.max", await db.users.count_documents({"business_ids": business_id}))
    user_id = str(ObjectId())
    doc = {"id": user_id, "email": email, "password_hash": hash_password(data.password), "name": data.name, "role": data.role, "business_ids": business_ids, "status": "active", "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    if POS_CORE_API_BASE_URL and business_ids:
        doc['pos_creation_pending'] = True
    await db.users.insert_one(doc)
    queued = False
    if POS_CORE_API_BASE_URL and business_ids:
        try:
            await pos_profile_updates.enqueue(doc, {}, data.password, user, operation="create")
            queued = True
            response.status_code = 202
        except Exception:
            # No POS request has run yet, so removing this incomplete local insert is safe.
            await db.users.delete_one({"id": user_id})
            raise
    biz_id = data.business_ids[0] if data.business_ids else None
    await create_audit_log(biz_id, user["id"], user["email"], "created", "user", user_id, {"email": email, "role": data.role, "pos_update_status": "pending" if queued else "not_configured"})
    created = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if queued:
        created["pos_update_status"] = "pending"
    return created

@user_router.put("/{user_id}")
async def update_user(user_id: str, data: UserUpdate, request: Request, response: Response):
    user = await get_current_user(request)
    if is_platform_staff(user):
        require_platform_permission(user, 'users.write')
    else:
        require_record_owner_candidate(user, 'users.write')
    existing = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")
    if user["role"] != "platform_admin" and (existing.get("role") in PLATFORM_ROLES | {"support_admin"} or not existing.get("business_ids")):
        raise HTTPException(status_code=403, detail="Only platform admins can edit platform accounts")
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if 'role' in update_data and ((existing.get('role') in PLATFORM_ROLES) != (update_data['role'] in PLATFORM_ROLES)):
        raise HTTPException(400, 'Platform and business identities are separate. Create a separate account for the other role type.')
    if existing.get('role') in PLATFORM_ROLES and update_data.get('business_ids', existing.get('business_ids', [])):
        raise HTTPException(400, 'Platform accounts cannot be assigned to customer businesses')
    original_sync_business_ids = await recover_business_assignments(db, existing, update_data, platform_admin=user["role"] == "platform_admin")
    if "email" in update_data:
        email = (update_data["email"] or "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Email is required")
        duplicate = await db.users.find_one({"email": email, "id": {"$ne": user_id}})
        if duplicate:
            raise HTTPException(status_code=400, detail="Email already exists")
        update_data["email"] = email
    if "password" in update_data:
        password = update_data.pop("password")
        if password:
            if len(password) < 8:
                raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
            update_data["password_hash"] = hash_password(password)
    if "role" in update_data:
        if update_data["role"] not in USER_ROLES:
            raise HTTPException(status_code=400, detail=f"role must be one of: {', '.join(USER_ROLES)}")
        if update_data["role"] != existing.get("role"):
            if user_id == user["id"]:
                raise HTTPException(status_code=400, detail="You cannot change your own role")
            if is_platform_staff(user) and not can_assign_role(user, update_data["role"]):
                raise HTTPException(status_code=403, detail="You cannot assign a role at or above your own authority")
    if update_data.get("status") == "inactive" and user_id == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    if "status" in update_data:
        update_data["status"] = (update_data["status"] or "active").lower()
        if update_data["status"] not in USER_STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(USER_STATUSES)}")
    if "business_ids" in update_data:
        update_data["business_ids"] = list(dict.fromkeys(update_data["business_ids"] or []))
        if is_platform_staff(user) and user['role'] != 'platform_admin' and set(update_data['business_ids']) != set(existing.get('business_ids', [])):
            raise HTTPException(403, 'Only platform admins can change business assignments')
        await validate_client_business_ids(user, update_data["business_ids"])
        for business_id in update_data["business_ids"]:
            await require_business_management(user, business_id, owner_only=True, target_role=update_data.get("role"))
            await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
    original_access_ids = original_sync_business_ids if user["role"] == "platform_admin" else existing.get("business_ids", [])
    await validate_business_access_many(user, original_access_ids)
    for business_id in original_access_ids:
        await require_business_management(user, business_id, owner_only=True, target_role=update_data.get("role"))
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
    # Forms echo unchanged account fields. They must not overwrite a different
    # role/status held by this identity in another POS business.
    for field in list(update_data):
        unchanged = set(update_data[field] or []) == set(existing.get(field) or []) if field == 'business_ids' else update_data[field] == existing.get(field)
        if unchanged:
            update_data.pop(field)
    if not update_data:
        return {key: value for key, value in existing.items() if key not in {'_id', 'password_hash'}}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    updated_user = {**existing, **update_data}
    if POS_CORE_API_BASE_URL and (updated_user.get("business_ids") or original_sync_business_ids):
        await pos_profile_updates.enqueue(existing, update_data, data.password, user, original_sync_business_ids=original_sync_business_ids)
        response.status_code = 202
        return {**{k: v for k, v in existing.items() if k not in {"_id", "password_hash"}}, "pos_update_status": "pending"}
    pos_push = []
    try:
        for target_business_id in updated_user.get("business_ids", []):
            pushed = await push_admin_user_to_pos(updated_user, data.password, target_business_id=target_business_id, previous_email=existing.get("email"))
            if pushed:
                pos_push.append(pushed)
    except HTTPException as exc:
        logger.warning("Could not push AdminCore user update to POS: %s", exc.detail)
        if POS_CORE_API_BASE_URL and updated_user.get("business_ids"):
            raise HTTPException(status_code=502, detail={"code": "POS_USER_UPDATE_FAILED", "message": "POS user update failed. The AdminCore profile was not saved; retry to finish any partial POS updates.", "cause": compact_bridge_error_detail(exc.detail)})
    local_update = {"$set": update_data}
    if any(field in update_data and update_data[field] != existing.get(field) for field in SESSION_REVOKING_FIELDS):
        local_update["$inc"] = {"token_version": 1}
    reducing_admin = existing.get('role') == 'platform_admin' and (update_data.get('role', existing['role']) != 'platform_admin' or update_data.get('status') == 'inactive')
    if reducing_admin:
        async with import_lease(db.pos_import_leases, 'platform', 'admin-identities'):
            fresh_actor = await get_current_user(request)
            require_roles(fresh_actor, 'platform_admin')
            if not await db.users.count_documents({'role': 'platform_admin', 'status': {'$ne': 'inactive'}, 'id': {'$ne': user_id}}):
                raise HTTPException(409, 'The last platform admin cannot be demoted or disabled')
            applied = await db.users.update_one({'id': user_id, 'role': 'platform_admin'}, local_update)
            if not applied.matched_count:
                raise HTTPException(409, 'The account changed during this update; refresh it')
    else:
        await db.users.update_one({"id": user_id}, local_update)
    if "email" in update_data:
        await db.businesses.update_many(
            {"id": {"$in": updated_user.get("business_ids", [])}, "pos_owner_email": existing.get("email")},
            {"$set": {"pos_owner_email": update_data["email"]}},
        )
    audit_changes = {k: v for k, v in update_data.items() if k != "password_hash"}
    await create_audit_log((update_data.get("business_ids") or existing.get("business_ids") or [None])[0], user["id"], user["email"], "updated", "user", user_id, {**audit_changes, "before": {k: existing.get(k) for k in audit_changes if k != "updated_at"}, "password_changed": "password_hash" in update_data, "pos_pushed": bool(pos_push)})
    return await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})

@user_router.post("/{user_id}/sync-pos", status_code=202)
async def sync_user_to_pos(user_id: str, request: Request):
    user = await get_current_user(request)
    if is_platform_staff(user):
        require_platform_permission(user, 'users.write')
    elif user["role"] not in ["business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    target_user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if not is_platform_staff(user):
        allowed_business_ids = set(user.get("business_ids", []))
        if not allowed_business_ids.intersection(set(target_user.get("business_ids", []))):
            raise HTTPException(status_code=403, detail="Access denied")
    recovered_changes = {}
    sync_business_ids = await recover_business_assignments(db, target_user, recovered_changes, platform_admin=user["role"] == "platform_admin")
    access_ids = sync_business_ids if user["role"] == "platform_admin" else target_user.get("business_ids", [])
    for business_id in access_ids:
        await require_business_management(user, business_id, owner_only=True)
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
    job = await db.pos_profile_jobs.find_one({"_id": user_id}, {"status": 1, "event": 1})
    if job and job.get("status") in {"pending", "running", "retrying", "failed"}:
        if job.get('status') == 'failed':
            intent = job.get('event') or {}
            intended_changes = intent.get('changes') or {}
            if is_platform_staff(user):
                if intended_changes.get('role') and not can_assign_role(user, intended_changes['role']):
                    raise HTTPException(403, 'Your role cannot authorize the pending role change')
                if 'business_ids' in intended_changes and user['role'] != 'platform_admin':
                    raise HTTPException(403, 'Only platform admins can authorize pending business assignment changes')
            intent_targets = set(intent.get('business_ids') or []) | set(intent.get('original_sync_business_ids', intent.get('original_business_ids') or []))
            for target in intent_targets:
                await require_business_management(user, target, owner_only=True, target_role=intended_changes.get('role'))
                await require_business_module_enabled(target, CORE_FEATURE_MODULES['users'])
            # The original actor may have lost access during a partial delivery.
            # This explicit, authorized retry adopts delivery without changing the
            # original intent, command receipts or initiating audit identity.
            await create_audit_log((access_ids or [None])[0], user['id'], user['email'],
                'sync_retry_authorized', 'user', user_id, {'operation_id': job.get('event', {}).get('operation_id')})
            await db.pos_profile_jobs.update_one({"_id": user_id, "status": "failed", 'event.operation_id': job.get('event', {}).get('operation_id')},
                {"$set": {"status": "retrying", "attempts": 0, "run_after": datetime.now(timezone.utc).isoformat(),
                          'event.delivery_actor_id': user['id'], 'event.delivery_actor_email': user['email']}})
        return {"message": "POS profile update queued", "status": "retrying" if job.get("status") == "failed" else job["status"]}
    if not sync_business_ids:
        raise HTTPException(status_code=400, detail="User is not assigned to a POS-syncable business")
    if not POS_CORE_API_BASE_URL:
        raise HTTPException(status_code=503, detail="POS bridge is not configured")
    result = await pos_profile_updates.enqueue(target_user, recovered_changes, None, user, operation="sync", original_sync_business_ids=sync_business_ids)
    return {"message": "POS profile update queued", **result}

@user_router.post("/{user_id}/mfa/reset")
async def reset_user_mfa(user_id: str, data: MfaReset, request: Request):
    user = await get_current_user(request)
    require_roles(user, "platform_admin")
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="Use the disable flow for your own account")
    reason = require_reason(data.reason)
    target = await db.users.find_one({"id": user_id}, {"_id": 0, "email": 1})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    await db.user_mfa.delete_one({"user_id": user_id})
    # Whoever held the old factor must sign in again.
    await db.users.update_one({"id": user_id}, {"$inc": {"token_version": 1}})
    await create_audit_log(None, user["id"], user["email"], "mfa_reset", "user", user_id, {"target": target.get("email"), "reason": reason})
    return {"message": "Two-factor authentication reset"}

@user_router.delete("/{user_id}")
async def delete_user(user_id: str, request: Request):
    user = await get_current_user(request)
    if is_platform_staff(user):
        require_platform_permission(user, 'users.write')
    else:
        require_record_owner_candidate(user, 'users.write')
    target = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if user["role"] != "platform_admin" and (target.get("role") in PLATFORM_ROLES | {"support_admin"} or not target.get("business_ids")):
        raise HTTPException(status_code=403, detail="Only platform admins can delete platform accounts")
    if target.get("role") == "platform_admin" and await db.users.count_documents({"role": "platform_admin", "status": {"$ne": "inactive"}, "id": {"$ne": user_id}}) == 0:
        raise HTTPException(status_code=409, detail="The last platform admin cannot be deleted")
    await validate_business_access_many(user, target.get("business_ids", []))
    for business_id in target.get("business_ids", []):
        await require_business_management(user, business_id, owner_only=True)
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
    if POS_CORE_API_BASE_URL and target.get('business_ids'):
        await pos_profile_updates.enqueue(target, {'status': 'inactive'}, None, user)
        return JSONResponse({'message': 'User deactivation queued; historical records are retained', 'status': 'pending'}, status_code=202)
    if target.get('pos_links'):
        raise HTTPException(409, 'Repair this user’s business assignments before deactivation; its POS account is still linked')
    if target.get('role') == 'platform_admin':
        async with import_lease(db.pos_import_leases, 'platform', 'admin-identities'):
            require_roles(await get_current_user(request), 'platform_admin')
            if not await db.users.count_documents({'role': 'platform_admin', 'status': {'$ne': 'inactive'}, 'id': {'$ne': user_id}}):
                raise HTTPException(409, 'The last platform admin cannot be deleted')
            result = await db.users.delete_one({'id': user_id, 'role': 'platform_admin'})
    else:
        result = await db.users.delete_one({"id": user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    await create_audit_log((target.get("business_ids") or [None])[0], user["id"], user["email"], "deleted", "user", user_id, {"before": {"email": target.get("email"), "role": target.get("role")}})
    return {"message": "User deleted"}


# ===================================================================
# SETTINGS ROUTES
# ===================================================================
settings_router = APIRouter(prefix="/settings", tags=["settings"])
SETTING_CATEGORIES = {**dict.fromkeys(['timezone', 'currency', 'language', 'date_format'], 'general'),
                      **dict.fromkeys(['email_notifications', 'sms_notifications', 'push_notifications'], 'notifications'),
                      **dict.fromkeys(['primary_color', 'business_tagline'], 'branding')}

@settings_router.get("/business/{business_id}")
async def list_settings(business_id: str, request: Request, category: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await require_business_admin_scope(user, business_id, permission="settings.read")
    if not is_platform_staff(user):
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["settings"])
    query = {"business_id": business_id}
    if category:
        query["category"] = category
    return await db.settings.find(query, {"_id": 0}).to_list(200)

@settings_router.put("/business/{business_id}/{key}")
async def update_setting(business_id: str, key: str, data: SettingUpdate, request: Request):
    user = await get_current_user(request)
    await require_business_admin_scope(user, business_id, permission="settings.write")
    if not is_platform_staff(user):
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["settings"])
    existing = await db.settings.find_one({"business_id": business_id, "key": key}, {"_id": 1})
    if not existing and key not in SETTING_CATEGORIES:
        raise HTTPException(404, "Setting not found")
    if key.endswith('_notifications') and data.value not in {'true', 'false'}:
        raise HTTPException(422, 'Notification preferences must be true or false')
    setting_id = existing['_id'] if existing else f'setting:{business_id}:{key}'
    await db.settings.update_one({'_id': setting_id}, {'$set': {'value': data.value, 'updated_at': datetime.now(timezone.utc).isoformat()},
        '$setOnInsert': {'id': str(ObjectId()), 'business_id': business_id, 'key': key, 'category': SETTING_CATEGORIES.get(key, 'general'),
                         'label': key.replace('_', ' ').title(), 'type': 'boolean' if key.endswith('_notifications') else 'text'}}, upsert=True)
    await create_audit_log(business_id, user["id"], user["email"], "updated", "setting", key, {"value": data.value})
    return {"message": "Setting updated"}


# ===================================================================
# FEATURE FLAGS ROUTES
# ===================================================================
ff_router = APIRouter(prefix="/feature-flags", tags=["feature-flags"])

@ff_router.get("/business/{business_id}")
async def list_feature_flags(business_id: str, request: Request):
    user = await get_current_user(request)
    await require_business_admin_scope(user, business_id, permission="businesses.read")
    if not is_platform_staff(user):
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["feature_flags"])
    return await db.feature_flags.find({"business_id": business_id}, {"_id": 0}).to_list(100)

@ff_router.post("/business/{business_id}")
async def create_feature_flag(business_id: str, data: FeatureFlagCreate, request: Request):
    user = await get_current_user(request)
    await require_business_admin_scope(user, business_id, permission="modules.write", owner_only=True)
    if not is_platform_staff(user):
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["feature_flags"])
    doc = {"id": str(ObjectId()), "business_id": business_id, "key": data.key, "name": data.name, "description": data.description, "enabled": data.enabled, "conditions": {}, "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.feature_flags.insert_one(doc)
    await create_audit_log(business_id, user["id"], user["email"], "created", "feature_flag", doc["id"], {"key": data.key})
    return {k: v for k, v in doc.items() if k != "_id"}

@ff_router.put("/{flag_id}")
async def update_feature_flag(flag_id: str, data: FeatureFlagUpdate, request: Request):
    user = await get_current_user(request)
    require_record_owner_candidate(user, "modules.write")
    existing = await db.feature_flags.find_one({"id": flag_id}, {"_id": 0, "business_id": 1})
    if not existing:
        raise HTTPException(status_code=404, detail="Feature flag not found")
    await require_business_admin_scope(user, existing.get("business_id"), permission="modules.write", owner_only=True)
    if not is_platform_staff(user):
        await require_business_module_enabled(existing.get("business_id"), CORE_FEATURE_MODULES["feature_flags"])
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.feature_flags.update_one({"id": flag_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Feature flag not found")
    flag = await db.feature_flags.find_one({"id": flag_id}, {"_id": 0})
    await create_audit_log(flag["business_id"], user["id"], user["email"], "updated", "feature_flag", flag_id, update_data)
    return flag

@ff_router.delete("/{flag_id}")
async def delete_feature_flag(flag_id: str, request: Request):
    user = await get_current_user(request)
    require_record_owner_candidate(user, "modules.write")
    flag = await db.feature_flags.find_one({"id": flag_id})
    if not flag:
        raise HTTPException(status_code=404, detail="Feature flag not found")
    await require_business_admin_scope(user, flag.get("business_id"), permission="modules.write", owner_only=True)
    if not is_platform_staff(user):
        await require_business_module_enabled(flag.get("business_id"), CORE_FEATURE_MODULES["feature_flags"])
    await db.feature_flags.delete_one({"id": flag_id})
    await create_audit_log(flag["business_id"], user["id"], user["email"], "deleted", "feature_flag", flag_id)
    return {"message": "Feature flag deleted"}


# ===================================================================
# AUDIT LOG ROUTES
# ===================================================================
audit_router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])

@audit_router.get("/business/{business_id}")
async def list_audit_logs(business_id: str, request: Request, limit: int = Query(50, ge=1, le=200), skip: int = Query(0, ge=0), entity_type: Optional[str] = None):
    user = await get_current_user(request)
    await require_business_admin_scope(user, business_id, permission="audit.read")
    if not is_platform_staff(user):
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["audit_logs"])
    query = {"business_id": business_id}
    if entity_type:
        query["entity_type"] = entity_type
    logs = await db.audit_logs.find(query, {"_id": 0}).sort([("created_at", -1), ("id", 1)]).skip(skip).limit(limit).to_list(limit)
    total = await db.audit_logs.count_documents(query)
    return {"logs": logs, "total": total}

@audit_router.get("")
async def list_all_audit_logs(request: Request, limit: int = Query(50, ge=1, le=200), skip: int = Query(0, ge=0), entity_type: Optional[str] = None):
    user = await get_current_user(request)
    require_platform_permission(user, "audit.read")
    query = {"entity_type": entity_type} if entity_type else {}
    logs = await db.audit_logs.find(query, {"_id": 0}).sort([("created_at", -1), ("id", 1)]).skip(skip).limit(limit).to_list(limit)
    total = await db.audit_logs.count_documents(query)
    return {"logs": logs, "total": total}


# ===================================================================
# INTEGRATION ROUTES
# ===================================================================
integration_router = APIRouter(prefix="/integrations", tags=["integrations"])

@integration_router.get("/business/{business_id}")
async def list_integrations(business_id: str, request: Request):
    user = await get_current_user(request)
    business = await require_business_admin_scope(user, business_id, permission="businesses.read")
    if not is_platform_staff(user):
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["integrations"])
    rows = await db.integrations.find({"business_id": business_id}, {"_id": 0}).to_list(100)
    can_read_secrets = has_platform_permission(user, "integrations.secrets.read") if is_platform_staff(user) else management_role(user, business_id, local_owner=business.get("owner_id") == user.get("id")) == "business_owner"
    if not can_read_secrets:
        # Integration credentials are for owners only; other members see status, not secrets.
        rows = [{k: v for k, v in row.items() if k != "config"} for row in rows]
    return rows

@integration_router.post("/business/{business_id}")
async def create_integration(business_id: str, data: IntegrationCreate, request: Request):
    user = await get_current_user(request)
    await require_business_admin_scope(user, business_id, permission="integrations.write", owner_only=True)
    if not is_platform_staff(user):
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["integrations"])
    doc = {"id": str(ObjectId()), "business_id": business_id, "slug": data.slug, "name": data.name, "type": data.type, "status": "inactive", "config": data.config, "webhook_url": "", "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.integrations.insert_one(doc)
    await create_audit_log(business_id, user["id"], user["email"], "created", "integration", doc["id"], {"name": data.name})
    return {k: v for k, v in doc.items() if k != "_id"}

@integration_router.put("/{integration_id}")
async def update_integration(integration_id: str, data: IntegrationUpdate, request: Request):
    user = await get_current_user(request)
    require_record_owner_candidate(user, "integrations.write")
    existing = await db.integrations.find_one({"id": integration_id}, {"_id": 0, "business_id": 1})
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
    await require_business_admin_scope(user, existing.get("business_id"), permission="integrations.write", owner_only=True)
    if not is_platform_staff(user):
        await require_business_module_enabled(existing.get("business_id"), CORE_FEATURE_MODULES["integrations"])
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.integrations.update_one({"id": integration_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Integration not found")
    # Credentials are never written to the audit trail, only which fields changed.
    await create_audit_log(existing.get("business_id"), user["id"], user["email"], "updated", "integration", integration_id, {"fields": sorted(k for k in update_data if k != "updated_at")})
    return await db.integrations.find_one({"id": integration_id}, {"_id": 0})

@integration_router.delete("/{integration_id}")
async def delete_integration(integration_id: str, request: Request):
    user = await get_current_user(request)
    require_record_owner_candidate(user, "integrations.write")
    intg = await db.integrations.find_one({"id": integration_id})
    if not intg:
        raise HTTPException(status_code=404, detail="Integration not found")
    await require_business_admin_scope(user, intg.get("business_id"), permission="integrations.write", owner_only=True)
    if not is_platform_staff(user):
        await require_business_module_enabled(intg.get("business_id"), CORE_FEATURE_MODULES["integrations"])
    await db.integrations.delete_one({"id": integration_id})
    await create_audit_log(intg["business_id"], user["id"], user["email"], "deleted", "integration", integration_id, {"before": {"name": intg.get("name"), "slug": intg.get("slug")}})
    return {"message": "Integration deleted"}


# ===================================================================
# DASHBOARD ROUTES
# ===================================================================
dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])

@dashboard_router.get("/stats")
async def get_dashboard_stats(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    base = await pos_business_filter(user, business_id)
    if is_platform_staff(user):
        require_platform_permission(user, "businesses.read")
    # Counts and money use exactly the same permission filters as the detail screens.
    metrics = {
        "total_products": ("products", "products"), "total_orders": ("sales-orders", "pos_sales_orders"),
        "total_bills": ("bills", "pos_bills"), "total_inventory_items": ("inventory", "pos_inventory_admin"),
        "total_staff_records": ("staff-shifts", "pos_staff_shifts"), "total_tables": ("tables", "pos_tables"),
        "total_kitchen_tickets": ("kitchen-kot", "pos_kitchen_kot"),
    }
    result = {}
    for key, (resource, collection) in metrics.items():
        try:
            query = await pos_read_filter(user, business_id, resource)
            result[key] = await db[collection].count_documents(query)
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            result[key] = None
    try:
        revenue_query = await pos_read_filter(user, business_id, "reports")
        revenue_query = {"$and": [revenue_query, {"status": {"$nin": ["void", "cancelled", "canceled"]}}]}
        revenue = await summarize_pos_collection(db.pos_bills, revenue_query, bill_revenue_expression())
        result.update(total_revenue=revenue["amount_total"], revenue_by_currency=revenue["amount_by_currency"],
                      invalid_revenue_amounts=revenue["invalid_amounts"])
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        result.update(total_revenue=None, revenue_by_currency=[], invalid_revenue_amounts=0)
    management_ids = []
    if not is_platform_staff(user):
        for target in ([business_id] if business_id else user.get("business_ids", [])):
            try:
                await require_business_admin_scope(user, target, permission="businesses.read")
                management_ids.append(target)
            except HTTPException as exc:
                if exc.status_code not in {403, 404}:
                    raise
    admin_query = ({"business_id": business_id} if business_id else {}) if is_platform_staff(user) else {"business_id": {"$in": management_ids}}
    allowed_ids = [business_id] if business_id else user.get("business_ids", [])
    business_query = ({"id": business_id} if business_id else {}) if is_platform_staff(user) else {"id": {"$in": allowed_ids}}
    users_query = ({"business_ids": business_id} if business_id else {}) if is_platform_staff(user) else {"business_ids": {"$in": management_ids}}
    can_audit = has_platform_permission(user, "audit.read") if is_platform_staff(user) else bool(management_ids)
    result.update(
        total_businesses=await db.businesses.count_documents(business_query),
        total_users=await db.users.count_documents(users_query),
        active_modules=await db.business_modules.count_documents({**admin_query, "enabled": True}),
        total_feature_flags=await db.feature_flags.count_documents(admin_query),
        total_integrations=await db.integrations.count_documents(admin_query),
        total_outlets=await db.outlets.count_documents({**base, "status": "active"}),
        recent_activity=await db.audit_logs.find(admin_query, {"_id": 0}).sort("created_at", -1).limit(10).to_list(10) if can_audit else [],
        revenue_definition="Invoice totals after refunds, including tax; void and cancelled invoices excluded. Not cash receipts.",
    )
    return result


# ===================================================================
# PLAN ROUTES
# ===================================================================
plan_router = APIRouter(prefix="/plans", tags=["plans"])

@plan_router.get("")
async def list_plans(request: Request):
    await get_current_user(request)
    return await db.plans.find({}, {"_id": 0}).sort("sort_order", 1).to_list(20)

@plan_router.post("")
async def create_plan(data: PlanCreate, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "plans.write")
    if await db.plans.find_one({"slug": data.slug}):
        raise HTTPException(status_code=400, detail="Plan slug already exists")
    doc = {"id": str(ObjectId()), "name": data.name, "slug": data.slug, "description": data.description, "is_active": True, "is_default": False, "trial_days": data.trial_days, "pricing": data.pricing, "limits": data.limits, "included_modules": data.included_modules, "features": data.features, "sort_order": data.sort_order, "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.plans.insert_one(doc)
    await create_audit_log(None, user["id"], user["email"], "created", "plan", doc["id"], {"name": data.name})
    return {k: v for k, v in doc.items() if k != "_id"}

@plan_router.get("/{plan_id}")
async def get_plan(plan_id: str, request: Request):
    await get_current_user(request)
    plan = await db.plans.find_one({"id": plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan

@plan_router.put("/{plan_id}")
async def update_plan(plan_id: str, data: PlanUpdate, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "plans.write")
    before = await db.plans.find_one({"id": plan_id}, {"_id": 0})
    if not before:
        raise HTTPException(status_code=404, detail="Plan not found")
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.plans.update_one({"id": plan_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Plan not found")
    await create_audit_log(None, user["id"], user["email"], "updated", "plan", plan_id, {**update_data, "before": {k: before.get(k) for k in update_data if k != "updated_at"}})
    return await db.plans.find_one({"id": plan_id}, {"_id": 0})

@plan_router.delete("/{plan_id}")
async def delete_plan(plan_id: str, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "plans.delete")
    active_subs = await db.subscriptions.count_documents({"plan_id": plan_id, "status": {"$in": ["active", "trial"]}})
    if active_subs > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete plan with {active_subs} active subscriptions")
    result = await db.plans.delete_one({"id": plan_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Plan not found")
    await create_audit_log(None, user["id"], user["email"], "deleted", "plan", plan_id)
    return {"message": "Plan deleted"}


# ===================================================================
# SUBSCRIPTION ROUTES
# ===================================================================
subscription_router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

@subscription_router.get("")
async def list_subscriptions(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    require_platform_permission(user, "subscriptions.read")
    query = {"business_id": business_id} if business_id else {}
    subs = await db.subscriptions.find(query, {"_id": 0}).to_list(200)
    for sub in subs:
        biz = await db.businesses.find_one({"id": sub["business_id"]}, {"_id": 0, "name": 1, "slug": 1})
        plan = await db.plans.find_one({"id": sub.get("plan_id")}, {"_id": 0, "name": 1, "slug": 1, "pricing": 1})
        sub["business_name"] = biz["name"] if biz else "Unknown"
        sub["business_slug"] = biz.get("slug", "") if biz else ""
        sub["plan_name"] = plan["name"] if plan else "Unknown"
        sub["plan_slug"] = plan.get("slug", "") if plan else ""
        sub["plan_pricing"] = plan.get("pricing", {}) if plan else {}
    return subs

async def record_subscription_event(business_id: str, event_type: str, user: dict, details: dict | None = None):
    await db.subscription_events.insert_one({
        "id": str(ObjectId()),
        "business_id": business_id,
        "event_type": event_type,
        "details": details or {},
        "created_by": user["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

@subscription_router.get("/business/{business_id}")
async def get_business_subscription(business_id: str, request: Request):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    sub = await db.subscriptions.find_one({"business_id": business_id}, {"_id": 0})
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")
    plan = await db.plans.find_one({"id": sub.get("plan_id")}, {"_id": 0})
    sub["plan"] = plan
    return sub

@subscription_router.post("")
async def create_subscription(data: SubscriptionCreate, request: Request):
    user = await get_current_user(request)
    # Plans and subscription state are commercial decisions: customers cannot grant them to themselves.
    require_platform_permission(user, "subscriptions.write")
    if data.status not in SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(SUBSCRIPTION_STATUSES)}")
    await validate_business_access(user, data.business_id)
    plan = await db.plans.find_one({"id": data.plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    existing = await db.subscriptions.find_one({"business_id": data.business_id})
    if existing:
        raise HTTPException(status_code=400, detail="Business already has a subscription. Update it instead.")
    if data.status not in ("trial", "trialing", "active"):
        await push_subscription_status_to_pos(data.business_id, data.status, None)
    now_ts = datetime.now(timezone.utc)
    trial_end = (now_ts + timedelta(days=plan.get("trial_days", 14))).isoformat() if data.status == "trial" else None
    period_end = (now_ts + timedelta(days=30 if data.billing_cycle == "monthly" else 365)).isoformat()
    doc = {"id": str(ObjectId()), "business_id": data.business_id, "plan_id": data.plan_id, "plan_slug": plan["slug"], "status": data.status, "billing_cycle": data.billing_cycle, "current_period_start": now_ts.isoformat(), "current_period_end": period_end, "trial_start": now_ts.isoformat() if data.status == "trial" else None, "trial_end": trial_end, "cancelled_at": None, "billing_provider": None, "billing_provider_id": None, "metadata": {}, "created_at": now_ts.isoformat(), "updated_at": now_ts.isoformat()}
    await db.subscriptions.insert_one(doc)
    await db.businesses.update_one({"id": data.business_id}, {"$set": {"plan": plan["slug"]}})
    await record_subscription_event(data.business_id, "TRIAL_STARTED" if data.status in ["trial", "trialing"] else "SUBSCRIPTION_CREATED", user, {"plan_id": data.plan_id, "status": data.status})
    await create_audit_log(data.business_id, user["id"], user["email"], "created", "subscription", doc["id"], {"plan": plan["name"], "status": data.status})
    return {k: v for k, v in doc.items() if k != "_id"}

@subscription_router.put("/{sub_id}")
async def update_subscription(sub_id: str, data: SubscriptionUpdate, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "subscriptions.write")
    sub = await db.subscriptions.find_one({"id": sub_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await validate_business_access(user, sub.get("business_id"))
    update_data = {k: v for k, v in data.model_dump().items() if v is not None and k != "reason"}
    if "status" in update_data and update_data["status"] not in SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(SUBSCRIPTION_STATUSES)}")
    if "status" in update_data and update_data["status"] != sub.get("status"):
        # Tell the customer application first; only record what the POS actually accepted.
        await push_subscription_status_to_pos(sub["business_id"], update_data["status"], update_data.get("current_period_end", sub.get("current_period_end")))
    if "plan_id" in update_data:
        plan = await db.plans.find_one({"id": update_data["plan_id"]}, {"_id": 0})
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        update_data["plan_slug"] = plan["slug"]
        await db.businesses.update_one({"id": sub["business_id"]}, {"$set": {"plan": plan["slug"]}})
        await record_subscription_event(sub["business_id"], "PLAN_CHANGED", user, {"from_plan_id": sub.get("plan_id"), "to_plan_id": update_data["plan_id"]})
    if "status" in update_data and update_data["status"] != sub.get("status"):
        await record_subscription_event(sub["business_id"], f"SUBSCRIPTION_{update_data['status'].upper()}", user, {"from_status": sub.get("status"), "to_status": update_data["status"]})
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.subscriptions.update_one({"id": sub_id}, {"$set": update_data})
    await create_audit_log(sub["business_id"], user["id"], user["email"], "updated", "subscription", sub_id, {**update_data, "before": {k: sub.get(k) for k in update_data if k != "updated_at"}, "reason": (data.reason or "").strip() or None})
    return await db.subscriptions.find_one({"id": sub_id}, {"_id": 0})

@subscription_router.post("/{sub_id}/cancel")
async def cancel_subscription(sub_id: str, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "subscriptions.write")
    sub = await db.subscriptions.find_one({"id": sub_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await validate_business_access(user, sub.get("business_id"))
    if sub.get("status") == "cancelled":
        return {"message": "Subscription already cancelled"}
    now_ts = datetime.now(timezone.utc).isoformat()
    await push_subscription_status_to_pos(sub["business_id"], "cancelled", sub.get("current_period_end"))
    await db.subscriptions.update_one({"id": sub_id}, {"$set": {"status": "cancelled", "cancelled_at": now_ts, "updated_at": now_ts}})
    await record_subscription_event(sub["business_id"], "SUBSCRIPTION_CANCELLED", user, {})
    await create_audit_log(sub["business_id"], user["id"], user["email"], "cancelled", "subscription", sub_id, {"before": {"status": sub.get("status")}})
    return {"message": "Subscription cancelled"}

# ===================================================================
# SUBSCRIPTION BILLING (Razorpay Subscriptions on Taskoora's own account)
# ===================================================================
billing_router = APIRouter(prefix="/billing", tags=["billing"])
# push_billing_state_to_pos is defined further down; it is looked up when a webhook is applied.
saas_billing_service = saas_billing.SaasBilling(
    db, lambda *args: push_billing_state_to_pos(*args), record_subscription_event, create_audit_log, logger, run=run_in_threadpool)


def billing_http_error(exc: "saas_billing.BillingError") -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


async def admin_business_for_pos(pos_business_id: str, tenant_id: Optional[str]) -> dict:
    query = {"pos_external_id": pos_business_id}
    if tenant_id:
        query["pos_tenant_id"] = tenant_id
    business = await db.businesses.find_one(query, {"_id": 0, "id": 1, "name": 1, "email": 1, "phone": 1})
    if not business:
        raise HTTPException(status_code=404, detail={"code": "BUSINESS_NOT_MANAGED", "message": "This business is not managed by Taskoora AdminCore"})
    return business


@billing_router.post("/razorpay/webhook")
async def razorpay_subscription_webhook(request: Request):
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return Response(status_code=400)
    status = await saas_billing_service.handle_webhook(raw, request.headers.get("x-razorpay-signature", ""), request.headers.get("x-razorpay-event-id", ""), body)
    return Response(status_code=status)


@billing_router.get("/pos/{pos_business_id}")
async def pos_billing_status(pos_business_id: str, request: Request, tenant_id: Optional[str] = Query(None)):
    require_pos_bridge_sync_key(request)
    business = await admin_business_for_pos(pos_business_id, tenant_id)
    return await saas_billing_service.status_for(business["id"])


@billing_router.post("/pos/{pos_business_id}/checkout")
async def pos_billing_checkout(pos_business_id: str, request: Request):
    require_pos_bridge_sync_key(request)
    data = await request.json()
    business = await admin_business_for_pos(pos_business_id, data.get("tenant_id"))
    actor = {"id": f"pos-owner:{str(data.get('actor_id') or '')[:60]}", "email": str(data.get("actor_email") or "")[:120]}
    try:
        return await saas_billing_service.start_checkout(business["id"], data.get("plan_slug"), data.get("billing_cycle") or "monthly", actor,
                                                         notify_email=data.get("notify_email"), notify_phone=data.get("notify_phone"))
    except saas_billing.BillingError as exc:
        raise billing_http_error(exc) from exc


@billing_router.post("/pos/{pos_business_id}/cancel")
async def pos_billing_cancel(pos_business_id: str, request: Request):
    require_pos_bridge_sync_key(request)
    data = await request.json()
    business = await admin_business_for_pos(pos_business_id, data.get("tenant_id"))
    actor = {"id": f"pos-owner:{str(data.get('actor_id') or '')[:60]}", "email": str(data.get("actor_email") or "")[:120]}
    try:
        return await saas_billing_service.cancel(business["id"], actor)
    except saas_billing.BillingError as exc:
        raise billing_http_error(exc) from exc


@billing_router.get("/status/{business_id}")
async def admin_billing_status(business_id: str, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "subscriptions.read")
    await validate_business_access(user, business_id)
    return await saas_billing_service.status_for(business_id)


class BillingCheckoutRequest(BaseModel):
    business_id: str
    plan_slug: str
    billing_cycle: str = "monthly"


@billing_router.post("/checkout")
async def admin_billing_checkout(data: BillingCheckoutRequest, request: Request):
    """A platform admin creates the payment page link for a customer (e.g. to send it by email)."""
    user = await get_current_user(request)
    require_platform_permission(user, "subscriptions.write")
    await validate_business_access(user, data.business_id)
    try:
        return await saas_billing_service.start_checkout(data.business_id, data.plan_slug, data.billing_cycle, user)
    except saas_billing.BillingError as exc:
        raise billing_http_error(exc) from exc


@billing_router.get("/payments")
async def admin_billing_payments(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    require_platform_permission(user, "subscriptions.read")
    if business_id:
        await validate_business_access(user, business_id)
    query = {"business_id": business_id} if business_id else {}
    return await db.billing_payments.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)


@subscription_router.post("/lifecycle/run")
async def run_subscription_lifecycle(request: Request):
    """Run the automatic expiry now (respects SUBSCRIPTION_AUTO_EXPIRY)."""
    user = await get_current_user(request)
    require_platform_permission(user, "subscriptions.write")
    outcome = await run_subscription_expiry()
    await create_audit_log(None, user["id"], user["email"], "ran", "subscription_lifecycle", None, outcome)
    return outcome

@subscription_router.get("/addons/catalog")
async def list_addon_catalog(request: Request):
    await get_current_user(request)
    return await db.addon_catalog.find({}, {"_id": 0}).sort("code", 1).to_list(200)

@subscription_router.get("/business/{business_id}/addons")
async def list_business_addons(business_id: str, request: Request):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    return await db.business_addons.find({"business_id": business_id}, {"_id": 0}).to_list(200)

@subscription_router.post("/addons")
async def grant_business_addon(data: BusinessAddonCreate, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "entitlements.write")
    await validate_business_access(user, data.business_id)
    addon = await db.addon_catalog.find_one({"id": data.addon_id}, {"_id": 0})
    if not addon:
        raise HTTPException(status_code=404, detail="Add-on not found")
    doc = {"id": str(ObjectId()), "business_id": data.business_id, "addon_id": data.addon_id, "addon_code": addon["code"], "quantity": max(1, int(data.quantity or 1)), "status": data.status, "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.business_addons.insert_one(doc)
    doc.pop("_id", None)
    await record_subscription_event(data.business_id, "ADDON_ADDED", user, {"addon_code": addon["code"], "quantity": doc["quantity"]})
    await create_audit_log(data.business_id, user["id"], user["email"], "created", "business_addon", doc["id"], {"addon": addon["code"]})
    return doc

@subscription_router.post("/entitlement-overrides")
async def upsert_entitlement_override(data: EntitlementOverrideCreate, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "entitlements.write")
    await validate_business_access(user, data.business_id)
    reason = require_reason(data.reason)
    previous = await db.business_entitlement_overrides.find_one({"business_id": data.business_id, "feature_code": data.feature_code}, {"_id": 0, "enabled": 1, "reason": 1})
    await db.business_entitlement_overrides.update_one(
        {"business_id": data.business_id, "feature_code": data.feature_code},
        {"$set": {"enabled": data.enabled, "reason": data.reason, "updated_by": user["id"], "updated_at": datetime.now(timezone.utc).isoformat()}, "$setOnInsert": {"id": str(ObjectId()), "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    await record_subscription_event(data.business_id, "ENTITLEMENT_OVERRIDDEN", user, {"feature_code": data.feature_code, "enabled": data.enabled})
    await create_audit_log(data.business_id, user["id"], user["email"], "overridden", "entitlement", data.feature_code, {"before": previous, "after": {"enabled": data.enabled}, "reason": reason})
    return {"message": "Entitlement override saved"}

@subscription_router.post("/limit-overrides")
async def upsert_limit_override(data: LimitOverrideCreate, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "entitlements.write")
    await validate_business_access(user, data.business_id)
    reason = require_reason(data.reason)
    previous = await db.business_limit_overrides.find_one({"business_id": data.business_id, "limit_code": data.limit_code}, {"_id": 0, "value": 1, "reason": 1})
    await db.business_limit_overrides.update_one(
        {"business_id": data.business_id, "limit_code": data.limit_code},
        {"$set": {"value": data.value, "reason": data.reason, "updated_by": user["id"], "updated_at": datetime.now(timezone.utc).isoformat()}, "$setOnInsert": {"id": str(ObjectId()), "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    await record_subscription_event(data.business_id, "LIMIT_OVERRIDDEN", user, {"limit_code": data.limit_code, "value": data.value})
    await create_audit_log(data.business_id, user["id"], user["email"], "overridden", "limit", data.limit_code, {"before": previous, "after": {"value": data.value}, "reason": reason})
    return {"message": "Limit override saved"}


# ===================================================================
# POS ADMIN OPERATIONS ROUTES
# ===================================================================
pos_admin_router = APIRouter(prefix="/pos-admin", tags=["pos-admin"])

POS_ADMIN_RESOURCES = {
    "sales-orders": {"priority": 1, "collection": "pos_sales_orders", "label": "POS Orders / Sales", "statuses": ["draft", "open", "paid", "refunded", "void"]},
    "payments": {"priority": 2, "collection": "pos_payments", "label": "Payments", "statuses": ["pending", "paid", "failed", "refunded", "reconciled"]},
    "bills": {"priority": 3, "collection": "pos_bills", "label": "Bills / Invoices", "statuses": ["draft", "open", "paid", "partial", "void", "refunded"]},
    "inventory": {"priority": 4, "collection": "pos_inventory_admin", "label": "Inventory Management", "statuses": ["active", "low_stock", "out_of_stock", "inactive"]},
    "customers": {"priority": 5, "collection": "pos_customers", "label": "Customers / CRM", "statuses": ["active", "vip", "blocked", "inactive"]},
    "tables": {"priority": 6, "collection": "pos_tables", "label": "Table Management", "statuses": ["available", "occupied", "reserved", "blocked"]},
    "reservations": {"priority": 7, "collection": "pos_reservations", "label": "Reservations", "statuses": ["reserved", "seated", "completed", "cancelled", "no_show"]},
    "kitchen-kot": {"priority": 8, "collection": "pos_kitchen_kot", "label": "Kitchen / KOT", "statuses": ["pending", "preparing", "ready", "served", "cancelled"]},
    "reports-analytics": {"priority": 9, "collection": "pos_reports_analytics", "label": "Reports & Analytics", "statuses": ["draft", "ready", "scheduled", "archived"]},
    "taxes-charges": {"priority": 10, "collection": "pos_taxes_charges", "label": "Taxes & Charges", "statuses": ["active", "inactive"]},
    "discounts-coupons": {"priority": 11, "collection": "pos_discounts_coupons", "label": "Discounts / Coupons", "statuses": ["active", "scheduled", "expired", "inactive"]},
    "staff-shifts": {"priority": 12, "collection": "pos_staff_shifts", "label": "Staff Shifts / Attendance", "statuses": ["scheduled", "clocked_in", "completed", "missed"]},
    "suppliers-purchasing": {"priority": 13, "collection": "pos_suppliers_purchasing", "label": "Suppliers / Purchasing", "statuses": ["draft", "ordered", "received", "cancelled"]},
    "expenses": {"priority": 14, "collection": "pos_expenses", "label": "Expenses", "statuses": ["pending", "approved", "paid", "rejected"]},
    "hardware-printers": {"priority": 15, "collection": "pos_hardware_printers", "label": "Hardware / Printer Settings", "statuses": ["online", "offline", "maintenance", "disabled"]},
    "role-permissions": {"priority": 16, "collection": "pos_role_permissions", "label": "Permissions Matrix", "statuses": ["active", "review", "disabled"]},
    "notifications": {"priority": 17, "collection": "pos_notifications", "label": "Notifications", "statuses": ["enabled", "paused", "disabled"]},
    "import-export": {"priority": 18, "collection": "pos_import_export", "label": "Import / Export", "statuses": ["queued", "processing", "completed", "failed"]},
    "integrations-webhooks": {"priority": 19, "collection": "pos_integrations_webhooks", "label": "Webhooks / Integrations", "statuses": ["connected", "failing", "disabled", "pending"]},
    "audit-security": {"priority": 20, "collection": "pos_audit_security", "label": "Audit & Security", "statuses": ["open", "reviewed", "resolved", "ignored"]},
    "qr-ordering": {"priority": 21, "collection": "pos_qr_ordering", "label": "QR Ordering", "statuses": ["active", "inactive", "completed", "closed"]},
    "central-kitchen": {"priority": 22, "collection": "pos_central_kitchen", "label": "Central Kitchen", "statuses": ["draft", "requested", "ordered", "received", "allocated", "dispatched", "completed", "cancelled"]},
}

def pos_resource_config(resource: str) -> dict:
    config = POS_ADMIN_RESOURCES.get(resource)
    if not config:
        raise HTTPException(status_code=404, detail="POS admin resource not found")
    return config

def require_pos_resource_writable(config: dict):
    require_local_write(config)

async def pos_business_filter(user: dict, business_id: Optional[str]) -> dict:
    query = {"tenant_scope_status": {"$ne": "quarantined"}, "source_present": {"$ne": False}, "deleted_at": None}
    if is_platform_staff(user):
        if business_id:
            await validate_business_access(user, business_id)
            query["business_id"] = business_id
        return query
    allowed = user.get("business_ids", [])
    if business_id:
        if business_id not in allowed:
            raise HTTPException(status_code=403, detail="Business access denied")
        await validate_business_access(user, business_id)
        query["business_id"] = business_id
    else:
        active = []
        for target in allowed:
            try:
                await validate_business_access(user, target)
                active.append(target)
            except HTTPException as exc:
                if exc.status_code not in {403, 404}:
                    raise
        query["business_id"] = {"$in": active}
    return query


async def pos_read_filter(user, business_id, resource):
    query = await pos_business_filter(user, business_id)
    if is_platform_staff(user):
        # Platform roles are explicit: operations cannot inspect payroll/marketing
        # merely because they can read the business directory.
        permission = {"payroll": "pos.payroll.read", "payslips": "pos.payroll.read", "marketing": "pos.marketing.read", "marketing-delivery": "pos.marketing.read",
                      "customer-profiles": "pos.customers.read", "customers": "pos.customers.read",
                      "loyalty-ledger": "pos.customers.read", "gift-cards": "pos.gift_cards.read", "gift-card-transactions": "pos.gift_cards.read", "tips": "pos.tips.read", "tip-payouts": "pos.tips.read"}.get(resource, "pos.read")
        if not has_platform_permission(user, permission):
            raise HTTPException(403, "This platform role cannot inspect these POS records")
        return query
    conditions = []
    for target in ([business_id] if business_id else user.get("business_ids", [])):
        try:
            business = await validate_business_access(user, target)
            outlets = tenant_read_scope(user, target, resource, local_owner=business.get("owner_id") == user.get("id"), connected=bool(business.get("pos_external_id")))
        except HTTPException:
            if business_id:
                raise
            continue
        condition = {"business_id": target}
        if outlets:
            local_rows = await db.outlets.find({"business_id": target, "pos_external_id": {"$in": outlets}}, {"_id": 0, "id": 1}).to_list(len(outlets))
            condition["$or"] = [{"outlet_id": {"$in": [row["id"] for row in local_rows]}},
                                {"metadata.outlet_id": {"$in": outlets}}, {"metadata.outletId": {"$in": outlets}}]
        conditions.append(condition)
    return {"$and": [query, {"$or": conditions}]} if conditions else {"id": {"$in": []}}

async def validate_pos_admin_business(user: dict, business_id: Optional[str]):
    await validate_business_access(user, business_id)

async def require_pos_bridge_access(user: dict, business_id: Optional[str]):
    if is_platform_staff(user):
        require_platform_permission(user, 'pos.read')
        return
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["pos_bridge"])

async def decorate_pos_records(records: list[dict]) -> list[dict]:
    business_ids = list({row.get("business_id") for row in records if row.get("business_id")})
    businesses = {}
    if business_ids:
        rows = await db.businesses.find({"id": {"$in": business_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(200)
        businesses = {row["id"]: row["name"] for row in rows}
    for row in records:
        row["business_name"] = businesses.get(row.get("business_id"), "All businesses" if not row.get("business_id") else "Unknown")
    return records

@pos_admin_router.get("/resources")
async def list_pos_admin_resources(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await validate_pos_admin_business(user, business_id)
    rows = []
    for key, value in POS_ADMIN_RESOURCES.items():
        try:
            await pos_read_filter(user, business_id, key)
        except HTTPException as exc:
            if exc.status_code == 403:
                continue
            raise
        module_slug = POS_RESOURCE_MODULES.get(key)
        enabled = True
        if business_id and module_slug:
            module_row = await ensure_business_module_row(business_id, module_slug)
            enabled = bool(module_row.get("enabled", False))
        if enabled or not business_id or (value.get("read_only") and is_platform_staff(user)):
            rows.append({"key": key, "module_slug": module_slug, **value})
    return rows

@pos_admin_router.get("/{resource}")
async def list_pos_admin_records(
    resource: str,
    request: Request,
    business_id: Optional[str] = Query(None),
    outlet_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    config = pos_resource_config(resource)
    user = await get_current_user(request)
    # Platform oversight of POS-owned snapshots does not enable a tenant feature.
    # Tenant users still need their business's module entitlement.
    if not (config.get("read_only") and is_platform_staff(user)):
        await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES.get(resource))
    query = await pos_read_filter(user, business_id, resource)
    if config.get("read_only"):
        query["source_present"] = {"$ne": False}
    bridge_resource = None
    if business_id and await is_pos_connected_business(business_id):
        bridge_resource = next(
            (
                key
                for key, value in POS_BRIDGE_RESOURCES.items()
                if value.get("pos_resource") == resource or key == resource
            ),
            None,
        )
        if bridge_resource:
            await cleanup_mismatched_pos_imports(bridge_resource, business_id)
    if outlet_id:
        query["outlet_id"] = outlet_id
    if status and status != "all":
        query["status"] = status
    if search:
        query["$or"] = [
            {"title": {"$regex": re.escape(search), "$options": "i"}},
            {"category": {"$regex": re.escape(search), "$options": "i"}},
            {"owner_name": {"$regex": re.escape(search), "$options": "i"}},
            {"contact": {"$regex": re.escape(search), "$options": "i"}},
        ]
    query = report_filter(query, date_from=date_from, date_to=date_to)

    collection = db[config["collection"]]
    has_filters = bool(outlet_id or search or date_from or date_to or (status and status != "all"))
    refresh_job = None
    if business_id and bridge_resource and (not has_filters or config.get("read_only")):
        job = await enqueue_pos_read_snapshot(bridge_resource, business_id)
        refresh_job = {"id": job["id"], "status": job["status"], "run_after": job.get("run_after"), "finished_at": job.get("finished_at")}
    # Direct callers also use this function outside FastAPI's query conversion.
    limit = limit if isinstance(limit, int) else 500
    offset = offset if isinstance(offset, int) else 0
    cursor = collection.find(query, {"_id": 0}).sort([("created_at", -1), ("id", 1)])
    if offset:
        cursor = cursor.skip(offset)
    records = await cursor.to_list(limit)
    summary = await summarize_pos_collection(collection, query, '$metadata.total_spent' if resource == 'customers' else None)
    total = summary["total"]
    return {
        "resource": resource,
        "label": config["label"],
        "statuses": config["statuses"],
        "records": await decorate_pos_records(records),
        "refresh_job": refresh_job,
        "pagination": {"limit": limit, "offset": offset, "total": total, "has_more": offset + len(records) < total},
        "summary": summary,
    }

@pos_admin_router.get("/payments/report")
async def get_pos_payment_report(request: Request, business_id: Optional[str] = Query(None), outlet_id: Optional[str] = Query(None), date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES["payments"])
    if business_id and await is_pos_connected_business(business_id):
        await cleanup_mismatched_pos_imports("payments", business_id)
    query = await pos_read_filter(user, business_id, "payments")
    query = report_filter(query, outlet_id=outlet_id, date_from=date_from, date_to=date_to)
    summary, groups = await asyncio.gather(summarize_pos_collection(db.pos_payments, query), payment_groups(db.pos_payments, query))
    if summary["invalid_amounts"]:
        groups["by_method"] = [{**row, "amount": None} for row in groups["by_method"]]
    return {
        "total_payments": summary["total"], "total_amount": summary["amount_total"],
        "amount_by_currency": summary["amount_by_currency"], "invalid_amounts": summary["invalid_amounts"],
        "by_status": summary["status_counts"], **groups,
        "definition": "Recorded payment amounts across matching statuses; not reconciled cash or net revenue",
    }

@pos_admin_router.get("/inventory/{record_id}/movements")
async def get_inventory_movements(record_id: str, request: Request):
    user = await get_current_user(request)
    scope = await pos_read_filter(user, None, "inventory")
    item = await db.pos_inventory_admin.find_one({"$and": [scope, {"id": record_id}]}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    await validate_pos_admin_business(user, item.get("business_id"))
    await require_business_module_enabled(item.get("business_id"), POS_RESOURCE_MODULES["inventory"])
    return await db.pos_inventory_movements.find({"$and": [scope, {"inventory_id": record_id, "business_id": item["business_id"]}]}, {"_id": 0}).sort("created_at", -1).to_list(200)

@pos_admin_router.get("/customers/{record_id}/order-history")
async def get_customer_order_history(record_id: str, request: Request, limit: int = Query(50, ge=1, le=250), offset: int = Query(0, ge=0)):
    user = await get_current_user(request)
    scope = await pos_read_filter(user, None, "customers")
    customer = await db.pos_customers.find_one({"$and": [scope, {"id": record_id}]}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    await validate_pos_admin_business(user, customer.get("business_id"))
    await require_business_module_enabled(customer.get("business_id"), POS_RESOURCE_MODULES["customers"])
    saved_history = customer.get("order_history") or []
    source_id = customer.get("pos_external_id") or customer.get("external_id")
    identities = [{"customer_id": customer["id"]}]
    if source_id:
        identities += [{"metadata.customer_id": source_id}, {"metadata.customerId": source_id}]
    order_scope = await pos_read_filter(user, customer["business_id"], "sales-orders")
    limit = limit if isinstance(limit, int) else 50
    offset = offset if isinstance(offset, int) else 0
    history_query = {"$and": [order_scope, {"$or": identities}]}
    matching_orders = await db.pos_sales_orders.find(history_query, {"_id": 0}).sort([("created_at", -1), ("id", 1)]).skip(offset).to_list(limit)
    total = await db.pos_sales_orders.count_documents(history_query)
    return {"saved_history": saved_history, "orders": matching_orders,
            "pagination": {"offset": offset, "limit": limit, "total": total, "has_more": offset + len(matching_orders) < total}}

@pos_admin_router.get("/kitchen-kot/performance")
async def get_kitchen_performance(request: Request, business_id: Optional[str] = Query(None), outlet_id: Optional[str] = Query(None), date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES["kitchen-kot"])
    if business_id and await is_pos_connected_business(business_id):
        await cleanup_mismatched_pos_imports("kitchen-tickets", business_id)
    query = await pos_read_filter(user, business_id, "kitchen-kot")
    query = report_filter(query, outlet_id=outlet_id, date_from=date_from, date_to=date_to)
    return await kitchen_groups(db.pos_kitchen_kot, query)

@pos_admin_router.get("/reports-analytics/summary")
async def get_reports_summary(request: Request, business_id: Optional[str] = Query(None), outlet_id: Optional[str] = Query(None), date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES["reports-analytics"])
    if business_id and await is_pos_connected_business(business_id):
        for bridge_resource in ["orders", "bills", "payments", "inventory", "staff-shifts", "tables", "kitchen-tickets", "products", "outlets"]:
            await cleanup_mismatched_pos_imports(bridge_resource, business_id)
    query = await pos_read_filter(user, business_id, "reports-analytics")
    current = report_filter(query, outlet_id=outlet_id)
    period = report_filter(query, outlet_id=outlet_id, date_from=date_from, date_to=date_to)
    sales_query = {**period, "status": {"$nin": ["void", "cancelled", "canceled"]}}
    sales, payments, inventory, staff, taxes, products, outlets = await asyncio.gather(
        summarize_pos_collection(db.pos_bills, sales_query, bill_revenue_expression()),
        summarize_pos_collection(db.pos_payments, period), summarize_pos_collection(db.pos_inventory_admin, current),
        summarize_pos_collection(db.pos_staff_shifts, current), summarize_pos_collection(db.pos_taxes_charges, period),
        summarize_pos_collection(db.products, current), summarize_pos_collection(db.outlets, current))
    def count_status(summary, value):
        return next((row["count"] for row in summary["status_counts"] if row["status"] == value), 0)
    return {
        "sales": {"count": sales["positive"], "total": sales["amount_total"], "amount_by_currency": sales["amount_by_currency"], "definition": "Invoice sales after refunds, tax included; void/cancelled invoices excluded; selected UTC dates"},
        "products": {"count": products["total"], "active": products["active"], "definition": "Current product catalog; not filtered by product creation date"},
        "inventory": {"count": inventory["total"], "low_stock": count_status(inventory, "low_stock"), "definition": "Current stock records; not a historical stock valuation"},
        "payments": {"count": payments["total"], "total": payments["amount_total"], "amount_by_currency": payments["amount_by_currency"], "definition": "Recorded payments in the selected UTC dates; not reconciled cash"},
        "staff": {"count": staff["total"], "definition": "Current staff accounts. Attendance has a separate view."},
        "outlets": {"count": outlets["total"], "active": count_status(outlets, "active"), "definition": "Current outlets"},
        "taxes": {"count": taxes["total"], "definition": "Recorded invoice tax entries in the selected UTC dates; not a statutory return"},
    }

def csv_escape(value) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace('"', '""') + '"'

@pos_admin_router.get("/reports-analytics/export")
async def export_reports(
    request: Request,
    business_id: Optional[str] = Query(None),
    report_type: str = Query("sales"),
    format: str = Query("csv"),
    outlet_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    user = await get_current_user(request)
    await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES["reports-analytics"])
    query = await pos_read_filter(user, business_id, "reports-analytics")
    source_map = {
        "sales": db.pos_bills,
        "products": db.products,
        "inventory": db.pos_inventory_admin,
        "payments": db.pos_payments,
        "staff": db.pos_staff_shifts,
        "outlets": db.outlets,
        "taxes": db.pos_taxes_charges,
    }
    collection = source_map.get(report_type)
    if collection is None:
        raise HTTPException(status_code=400, detail="Unsupported report_type")
    if format not in {"csv", "pdf"}:
        raise HTTPException(status_code=400, detail="Unsupported export format")
    dated = report_type in {"sales", "payments", "taxes"}
    query = report_filter(query, outlet_id=outlet_id, date_from=date_from if dated else None, date_to=date_to if dated else None)
    if report_type == "sales":
        query["status"] = {"$nin": ["void", "cancelled", "canceled"]}
    if report_type in {"sales", "payments"}:
        totals = await summarize_pos_collection(collection, query, bill_revenue_expression() if report_type == "sales" else None)
        if totals["invalid_amounts"]:
            raise HTTPException(409, "A source record contains an invalid amount; the report was withheld")
    cursor = collection.find(query, {"_id": 0}).sort([("created_at", -1), ("id", -1)])
    scope_label = (f'UTC dates: {date_from or "beginning"} to {date_to or "present"}' if dated else 'Current records; creation-date filters do not apply')
    if isinstance(outlet_id, str) and outlet_id:
        scope_label += f' · Outlet {outlet_id}'
    if report_type == "sales":
        scope_label += ' · Invoice sales after refunds, tax included; void/cancelled excluded'
    async def make_report(rows):
        if report_type == "sales":
            rows = [{**row, "metadata": {**(row.get("metadata") or {}), "total": str(bill_revenue(row))}} for row in rows]
        business_ids = {row.get("business_id") for row in rows if row.get("business_id")}
        if business_id:
            business_ids.add(business_id)
        business_rows = await db.businesses.find({"id": {"$in": list(business_ids)}},
            {"_id": 0, "id": 1, "name": 1}).to_list(len(business_ids)) if business_ids else []
        business_names = {business["id"]: business["name"] for business in business_rows}
        report = build_report(report_type, rows,
            business_name=business_names.get(business_id, "Selected business") if business_id else "All permitted businesses",
            business_names=None if business_id else business_names)
        report["scope_label"] = scope_label
        return report
    if format == "csv":
        async def stream_csv():
            index, first = 1, True
            try:
                while True:
                    batch = await cursor.to_list(500)
                    if not batch:
                        if first:
                            yield render_report_csv(await make_report([]))
                        break
                    yield render_report_csv(await make_report(batch), start_index=index, include_header=first)
                    index, first = index + len(batch), False
            finally:
                await cursor.close()
        return StreamingResponse(stream_csv(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={report_type}-report.csv"})
    rows = await cursor.to_list(10001)
    await cursor.close()
    if len(rows) > 10000:
        raise HTTPException(status_code=413, detail="PDF supports up to 10,000 records. Narrow the date/outlet scope or use CSV for the complete dataset.")
    report = await make_report(rows)
    if format == "pdf":
        content = await run_in_threadpool(render_report_pdf, report)
        return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={report_type}-report.pdf"})
    return Response(content=render_report_csv(report), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={report_type}-report.csv"})

@pos_admin_router.get("/{resource}/{record_id}")
async def get_pos_admin_record(resource: str, record_id: str, request: Request):
    config = pos_resource_config(resource)
    user = await get_current_user(request)
    query = await pos_read_filter(user, None, resource)
    record = await db[config["collection"]].find_one({"$and": [query, {"id": record_id}]}, {"_id": 0})
    if not record or record.get("source_present") is False:
        raise HTTPException(status_code=404, detail="POS admin record not found")
    await validate_pos_admin_business(user, record.get("business_id"))
    if not (config.get("read_only") and is_platform_staff(user)):
        await require_business_module_enabled(record.get("business_id"), POS_RESOURCE_MODULES.get(resource))
    decorated = await decorate_pos_records([record])
    return decorated[0]

async def create_inventory_movement(record: dict, user: dict, movement_type: Optional[str], movement_quantity: Optional[float], note: str = ""):
    if not movement_type or movement_quantity in [None, ""]:
        return
    now_ts = datetime.now(timezone.utc).isoformat()
    qty = float(movement_quantity or 0)
    await db.pos_inventory_movements.insert_one({
        "id": str(ObjectId()),
        "inventory_id": record["id"],
        "business_id": record.get("business_id"),
        "outlet_id": record.get("outlet_id"),
        "movement_type": movement_type,
        "quantity": qty,
        "notes": note,
        "created_by": user["id"],
        "created_at": now_ts,
    })

@pos_admin_router.post("/{resource}")
async def create_pos_admin_record(resource: str, data: POSAdminRecord, request: Request):
    config = pos_resource_config(resource)
    user = await get_current_user(request)
    require_pos_resource_writable(config)
    require_roles(user, "platform_admin", "business_owner", "manager")
    if POS_RESOURCE_MODULES.get(resource) and not data.business_id:
        raise HTTPException(status_code=400, detail="business_id is required")
    await validate_pos_admin_business(user, data.business_id)
    require_local_write(config, connected=await is_pos_connected_business(data.business_id))
    await require_business_module_enabled(data.business_id, POS_RESOURCE_MODULES.get(resource))
    now_ts = datetime.now(timezone.utc).isoformat()
    status = data.status if data.status in config["statuses"] else config["statuses"][0]
    doc = {
        **data.model_dump(),
        "id": str(ObjectId()),
        "resource": resource,
        "status": status,
        "created_by": user["id"],
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    if resource == "inventory":
        movement_qty = float(data.movement_quantity or 0)
        if data.amount is None and movement_qty:
            doc["amount"] = movement_qty if data.movement_type != "stock_out" else 0
        if doc.get("reorder_level") is not None and float(doc.get("amount") or 0) <= float(doc.get("reorder_level") or 0):
            doc["status"] = "low_stock"
    await db[config["collection"]].insert_one(doc)
    if resource == "inventory":
        await create_inventory_movement(doc, user, data.movement_type, data.movement_quantity, data.notes)
    await create_audit_log(doc.get("business_id"), user["id"], user["email"], "created", f"pos_admin:{resource}", doc["id"], {"title": doc["title"]})
    return {k: v for k, v in doc.items() if k != "_id"}

@pos_admin_router.put("/{resource}/{record_id}")
async def update_pos_admin_record(resource: str, record_id: str, data: POSAdminRecordUpdate, request: Request):
    config = pos_resource_config(resource)
    user = await get_current_user(request)
    require_pos_resource_writable(config)
    require_roles(user, "platform_admin", "business_owner", "manager")
    collection = db[config["collection"]]
    existing = await collection.find_one({"id": record_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="POS admin record not found")
    await validate_pos_admin_business(user, existing.get("business_id"))
    require_local_write(config, existing, connected=await is_pos_connected_business(existing.get("business_id")))
    await require_business_module_enabled(existing.get("business_id"), POS_RESOURCE_MODULES.get(resource))
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if "business_id" in update_data:
        await validate_pos_admin_business(user, update_data.get("business_id"))
        await require_business_module_enabled(update_data.get("business_id"), POS_RESOURCE_MODULES.get(resource))
    if "status" in update_data and update_data["status"] not in config["statuses"]:
        raise HTTPException(status_code=400, detail=f"Invalid status for {config['label']}")
    if resource == "inventory" and update_data.get("movement_quantity") is not None:
        current_stock = float(existing.get("amount") or 0)
        movement_qty = float(update_data.get("movement_quantity") or 0)
        movement_type = update_data.get("movement_type") or "adjustment"
        if movement_type == "stock_in":
            update_data["amount"] = current_stock + movement_qty
        elif movement_type in ["stock_out", "wastage"]:
            update_data["amount"] = max(0, current_stock - movement_qty)
        elif movement_type == "adjustment":
            update_data["amount"] = movement_qty
    if resource == "inventory":
        projected_stock = float(update_data.get("amount", existing.get("amount") or 0) or 0)
        reorder_level = update_data.get("reorder_level", existing.get("reorder_level"))
        if reorder_level is not None and projected_stock <= float(reorder_level or 0):
            update_data["status"] = "low_stock"
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    await collection.update_one({"id": record_id}, {"$set": update_data})
    updated = await collection.find_one({"id": record_id}, {"_id": 0})
    if resource == "inventory":
        await create_inventory_movement(updated, user, update_data.get("movement_type"), update_data.get("movement_quantity"), update_data.get("notes", ""))
    await create_audit_log(updated.get("business_id"), user["id"], user["email"], "updated", f"pos_admin:{resource}", record_id, update_data)
    return updated

@pos_admin_router.delete("/{resource}/{record_id}")
async def delete_pos_admin_record(resource: str, record_id: str, request: Request):
    config = pos_resource_config(resource)
    user = await get_current_user(request)
    require_pos_resource_writable(config)
    require_roles(user, "platform_admin", "business_owner", "manager")
    collection = db[config["collection"]]
    existing = await collection.find_one({"id": record_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="POS admin record not found")
    await validate_pos_admin_business(user, existing.get("business_id"))
    require_local_write(config, existing, connected=await is_pos_connected_business(existing.get("business_id")))
    await require_business_module_enabled(existing.get("business_id"), POS_RESOURCE_MODULES.get(resource))
    await collection.delete_one({"id": record_id})
    await create_audit_log(existing.get("business_id"), user["id"], user["email"], "deleted", f"pos_admin:{resource}", record_id, {"before": {"title": existing.get("title"), "status": existing.get("status"), "amount": existing.get("amount")}})
    return {"message": "POS admin record deleted"}


# ===================================================================
# SAAS + POS CONTROL CENTER ROUTES
# ===================================================================
control_center_router = APIRouter(prefix="/control-center", tags=["control-center"])

SAAS_CONTROL_MODULES = [
    {"key": "businesses", "label": "Businesses", "path": "/businesses", "collection": "businesses"},
    {"key": "users", "label": "Users", "path": "/users", "collection": "users"},
    {"key": "roles", "label": "Roles", "path": "/users", "collection": "users"},
    {"key": "modules", "label": "Modules", "path": "/modules", "collection": "modules"},
    {"key": "plans", "label": "Plans", "path": "/plans", "collection": "plans"},
    {"key": "subscriptions", "label": "Subscriptions", "path": "/subscriptions", "collection": "subscriptions"},
    {"key": "billing", "label": "Billing", "path": "/subscriptions", "collection": "subscriptions"},
    {"key": "feature-flags", "label": "Feature Flags", "path": "/feature-flags", "collection": "feature_flags"},
    {"key": "audit-logs", "label": "Audit Logs", "path": "/audit-logs", "collection": "audit_logs"},
    {"key": "integrations", "label": "Integrations", "path": "/integrations", "collection": "integrations"},
]

async def count_for_business(collection_name: str, business_id: Optional[str]) -> int:
    if collection_name == "businesses":
        return await db.businesses.count_documents({"id": business_id} if business_id else {})
    if collection_name == "users":
        return await db.users.count_documents({"business_ids": business_id} if business_id else {})
    if collection_name == "modules":
        return await db.business_modules.count_documents({"business_id": business_id, "enabled": True}) if business_id else await db.modules.count_documents({})
    query = {"business_id": business_id} if business_id else {}
    if collection_name.startswith("pos_"):
        query["source_present"] = {"$ne": False}
    return await db[collection_name].count_documents(query)

@control_center_router.get("/overview")
async def get_control_center_overview(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    if is_platform_staff(user):
        require_platform_permission(user, "businesses.read")
    elif business_id:
        await require_business_admin_scope(user, business_id, permission="businesses.read")
    else:
        authorized = []
        for target in user.get("business_ids", []):
            try:
                await require_business_admin_scope(user, target, permission="businesses.read")
                authorized.append(target)
            except HTTPException as exc:
                if exc.status_code not in {403, 404}:
                    raise
        user = {**user, "business_ids": authorized}
    base_filter = await pos_business_filter(user, business_id)
    scoped_business_id = base_filter.get("business_id") if isinstance(base_filter.get("business_id"), str) else business_id
    scoped_business_ids = [scoped_business_id] if scoped_business_id else []
    if not scoped_business_id and not is_platform_staff(user):
        scoped_business_ids = user.get("business_ids", [])

    async def count_for_control_scope(collection_name: str) -> int:
        if scoped_business_id or is_platform_staff(user):
            return await count_for_business(collection_name, scoped_business_id)
        if collection_name == "businesses":
            return await db.businesses.count_documents({"id": {"$in": scoped_business_ids}})
        if collection_name == "users":
            return await db.users.count_documents({"business_ids": {"$in": scoped_business_ids}})
        if collection_name == "modules":
            return await db.business_modules.count_documents({"business_id": {"$in": scoped_business_ids}, "enabled": True})
        query = {"business_id": {"$in": scoped_business_ids}}
        if collection_name.startswith("pos_"):
            query["source_present"] = {"$ne": False}
        return await db[collection_name].count_documents(query)

    active_sub_query = {"status": {"$in": ["active", "trial"]}}
    if scoped_business_id:
        active_sub_query["business_id"] = scoped_business_id
    elif not is_platform_staff(user):
        active_sub_query["business_id"] = {"$in": scoped_business_ids}
    active_subscriptions = await db.subscriptions.find(active_sub_query, {"_id": 0}).to_list(500)
    plan_ids = list({sub.get("plan_id") for sub in active_subscriptions if sub.get("plan_id")})
    plans = await db.plans.find({"id": {"$in": plan_ids}}, {"_id": 0, "id": 1, "pricing": 1}).to_list(100) if plan_ids else []
    plan_by_id = {plan["id"]: plan for plan in plans}
    mrr = 0
    for sub in active_subscriptions:
        plan = plan_by_id.get(sub.get("plan_id"), {})
        pricing = plan.get("pricing", {})
        monthly = pricing.get("monthly", 0)
        yearly = pricing.get("yearly", 0)
        mrr += (yearly / 12) if sub.get("billing_cycle") == "yearly" and yearly else monthly

    saas_modules = []
    for module in SAAS_CONTROL_MODULES:
        if module["key"] == "roles":
            if scoped_business_id:
                role_query = {"business_ids": scoped_business_id}
            elif not is_platform_staff(user):
                role_query = {"business_ids": {"$in": scoped_business_ids}}
            else:
                role_query = {}
            roles = await db.users.distinct("role", role_query)
            count = len([role for role in roles if role])
        elif module["key"] == "billing":
            count = len(active_subscriptions)
        else:
            count = await count_for_control_scope(module["collection"])
        saas_modules.append({**module, "count": count})

    pos_modules = []
    pos_total_records = 0
    for key, config in POS_ADMIN_RESOURCES.items():
        try:
            query = await pos_read_filter(user, business_id, key)
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            continue
        count = await db[config["collection"]].count_documents(query)
        pos_total_records += count
        pos_modules.append({
            "key": key,
            "label": config["label"],
            "path": f"/pos-admin/{key}",
            "count": count,
            "statuses": config["statuses"],
        })

    if scoped_business_id:
        business_query = {"id": scoped_business_id}
        audit_query = {"business_id": scoped_business_id}
    elif not is_platform_staff(user):
        business_query = {"id": {"$in": scoped_business_ids}}
        audit_query = {"business_id": {"$in": scoped_business_ids}}
    else:
        business_query = {}
        audit_query = {}
    recent_activity = await db.audit_logs.find(audit_query, {"_id": 0}).sort("created_at", -1).limit(12).to_list(12)

    return {
        "scope": "business" if scoped_business_id else "platform",
        "business_id": scoped_business_id or "",
        "summary": {
            "businesses": await db.businesses.count_documents(business_query),
            "users": await count_for_control_scope("users"),
            "active_subscriptions": len(active_subscriptions),
            "monthly_recurring_revenue": round(mrr, 2),
            "saas_modules": len(SAAS_CONTROL_MODULES),
            "pos_modules": len(pos_modules),
            "pos_records": pos_total_records,
        },
        "saas_modules": saas_modules,
        "pos_modules": pos_modules,
        "recent_activity": recent_activity,
    }


# ===================================================================
# POS PROJECT BRIDGE ROUTES
# ===================================================================
pos_bridge_router = APIRouter(prefix="/pos-bridge", tags=["pos-bridge"])

POS_BRIDGE_RESOURCES = {
    "businesses": {"endpoint": "sync/export/businesses", "endpoint_candidates": ["sync/export/businesses", "businesses"], "mode": "core", "collection": "businesses", "label": "Businesses"},
    "outlets": {"endpoint": "sync/export/outlets", "endpoint_candidates": ["sync/export/outlets", "outlets"], "mode": "core", "collection": "outlets", "label": "Outlets"},
    "products": {"endpoint": "sync/export/products", "endpoint_candidates": ["sync/export/products", "products"], "mode": "core", "collection": "products", "label": "Products"},
    "orders": {"endpoint": "sync/export/orders", "endpoint_candidates": ["sync/export/orders", "orders"], "mode": "pos_admin", "collection": "pos_sales_orders", "pos_resource": "sales-orders", "label": "Orders"},
    "bills": {"endpoint": "sync/export/bills", "endpoint_candidates": ["sync/export/bills", "billing", "bills"], "mode": "pos_admin", "collection": "pos_bills", "pos_resource": "bills", "label": "Bills"},
    "payments": {"endpoint": "sync/export/payments", "endpoint_candidates": ["sync/export/payments", "billing", "bills", "payments", "billing/payments"], "mode": "pos_admin", "collection": "pos_payments", "pos_resource": "payments", "label": "Payments"},
    "tables": {"endpoint": "sync/export/tables", "endpoint_candidates": ["sync/export/tables", "table-management", "tables"], "mode": "pos_admin", "collection": "pos_tables", "pos_resource": "tables", "label": "Tables"},
    "reservations": {"endpoint": "sync/export/reservations", "endpoint_candidates": ["sync/export/reservations", "reservations", "table-reservations"], "mode": "pos_admin", "collection": "pos_reservations", "pos_resource": "reservations", "label": "Reservations"},
    "customers": {"endpoint": "sync/export/customers", "endpoint_candidates": ["sync/export/customers", "customers", "crm/customers", "orders", "billing", "bills"], "mode": "pos_admin", "collection": "pos_customers", "pos_resource": "customers", "label": "Customers"},
    "kitchen-tickets": {"endpoint": "sync/export/kot", "endpoint_candidates": ["sync/export/kot", "kot", "kitchen", "billing"], "mode": "pos_admin", "collection": "pos_kitchen_kot", "pos_resource": "kitchen-kot", "label": "Kitchen Tickets"},
    "inventory": {"endpoint": "sync/export/inventory", "endpoint_candidates": ["sync/export/inventory", "inventory"], "mode": "pos_admin", "collection": "pos_inventory_admin", "pos_resource": "inventory", "label": "Inventory"},
    "staff-shifts": {"endpoint": "sync/export/staff", "endpoint_candidates": ["sync/export/staff", "staff-shifts", "staff", "attendance", "users"], "mode": "pos_admin", "collection": "pos_staff_shifts", "pos_resource": "staff-shifts", "label": "Staff Shifts"},
    "reports": {"endpoint": "sync/export/reports", "endpoint_candidates": ["sync/export/reports", "dashboard/stats"], "mode": "pos_admin", "collection": "pos_reports_analytics", "pos_resource": "reports-analytics", "label": "Reports"},
    "qr-ordering": {"endpoint": "sync/export/qr", "endpoint_candidates": ["sync/export/qr"], "mode": "pos_admin", "collection": "pos_qr_ordering", "pos_resource": "qr-ordering", "label": "QR Ordering"},
    "central-kitchen": {"endpoint": "sync/export/central-kitchen", "endpoint_candidates": ["sync/export/central-kitchen"], "mode": "pos_admin", "collection": "pos_central_kitchen", "pos_resource": "central-kitchen", "label": "Central Kitchen"},
    "taxes-charges": {"endpoint": "sync/export/taxes", "endpoint_candidates": ["sync/export/taxes"], "mode": "pos_admin", "collection": "pos_taxes_charges", "pos_resource": "taxes-charges", "label": "Taxes & Charges"},
    "discounts-coupons": {"endpoint": "sync/export/discounts", "endpoint_candidates": ["sync/export/discounts"], "mode": "pos_admin", "collection": "pos_discounts_coupons", "pos_resource": "discounts-coupons", "label": "Discounts / Coupons"},
    "suppliers-purchasing": {"endpoint": "sync/export/suppliers", "endpoint_candidates": ["sync/export/suppliers"], "mode": "pos_admin", "collection": "pos_suppliers_purchasing", "pos_resource": "suppliers-purchasing", "label": "Suppliers / Purchasing"},
    "expenses": {"endpoint": "sync/export/expenses", "endpoint_candidates": ["sync/export/expenses"], "mode": "pos_admin", "collection": "pos_expenses", "pos_resource": "expenses", "label": "Expenses"},
    "hardware-printers": {"endpoint": "sync/export/hardware", "endpoint_candidates": ["sync/export/hardware"], "mode": "pos_admin", "collection": "pos_hardware_printers", "pos_resource": "hardware-printers", "label": "Hardware / Printers"},
    "role-permissions": {"endpoint": "sync/export/permissions", "endpoint_candidates": ["sync/export/permissions"], "mode": "pos_admin", "collection": "pos_role_permissions", "pos_resource": "role-permissions", "label": "Role Permissions"},
    "notifications": {"endpoint": "sync/export/notifications", "endpoint_candidates": ["sync/export/notifications"], "mode": "pos_admin", "collection": "pos_notifications", "pos_resource": "notifications", "label": "Notifications"},
    "import-export": {"endpoint": "sync/export/import-export", "endpoint_candidates": ["sync/export/import-export"], "mode": "pos_admin", "collection": "pos_import_export", "pos_resource": "import-export", "label": "Import / Export"},
    "integrations-webhooks": {"endpoint": "sync/export/webhooks", "endpoint_candidates": ["sync/export/webhooks"], "mode": "pos_admin", "collection": "pos_integrations_webhooks", "pos_resource": "integrations-webhooks", "label": "Integrations / Webhooks"},
    "audit-security": {"endpoint": "sync/export/audit-security", "endpoint_candidates": ["sync/export/audit-security"], "mode": "pos_admin", "collection": "pos_audit_security", "pos_resource": "audit-security", "label": "Audit & Security"},
}

register_operations(POS_ADMIN_RESOURCES, POS_BRIDGE_RESOURCES, POS_RESOURCE_MODULES)
POS_BRIDGE_RESOURCES['privacy-erasures'] = {
    'endpoint': 'sync/export/privacy-erasures', 'mode': 'privacy', 'collection': 'pos_privacy_erasures',
    'label': 'Privacy erasures', 'internal_only': True,
}

def projection_privacy():
    previous = tuple(value.strip() for value in os.environ.get('POS_PRIVACY_PREVIOUS_KEYS', '').split(',') if value.strip())
    return ProjectionPrivacy(db, POS_CORE_API_KEY, [config['collection'] for config in POS_ADMIN_RESOURCES.values()], previous)

def pos_bridge_resource(resource: str) -> dict:
    if resource == "staff":
        resource = "staff-shifts"
    config = POS_BRIDGE_RESOURCES.get(resource)
    if not config:
        raise HTTPException(status_code=404, detail="POS bridge resource not found")
    return config

def ensure_pos_bridge_config():
    if not POS_CORE_API_BASE_URL:
        raise HTTPException(status_code=503, detail="POS bridge is not configured. Set POS_CORE_API_BASE_URL on the admin backend.")

def normalize_pos_bridge_rows(payload):
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if isinstance(data, dict):
        for key in ["records", "items", "results", "rows", "businesses", "outlets", "products", "orders", "bills", "payments", "tables", "qr_codes", "central_kitchen", "taxes", "discounts", "suppliers", "expenses", "hardware", "permissions", "notifications", "webhooks", "audit_security", "settings", "tickets", "kots", "inventory", "customers", "reservations", "staff", "shifts"]:
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return data if isinstance(data, list) else []

def copy_row_scope(target: dict, source: dict) -> dict:
    for key in ["business_id", "businessId", "tenant_id", "tenantId", "pos_business_id", "pos_tenant_id"]:
        if source.get(key) and not target.get(key):
            target[key] = source.get(key)
    return target

SCOPE_DERIVED_POS_RESOURCES = {"payments", "customers", "kitchen-tickets", "reports"}

async def rows_with_expected_scope(resource: str, rows: list, business_id: Optional[str]) -> list:
    if not business_id or resource == "businesses" or resource not in SCOPE_DERIVED_POS_RESOURCES:
        return rows
    scope = await expected_pos_scope_for_business(business_id)
    scoped_rows = []
    for row in rows:
        if not isinstance(row, dict):
            scoped_rows.append(row)
            continue
        next_row = dict(row)
        if not pos_row_business_id(next_row):
            next_row["business_id"] = scope["business_id"]
            next_row["businessId"] = scope["business_id"]
        if not pos_row_tenant_id(next_row):
            next_row["tenant_id"] = scope["tenant_id"]
            next_row["tenantId"] = scope["tenant_id"]
        scoped_rows.append(next_row)
    return scoped_rows

def derive_payment_rows(rows: list) -> list:
    payments = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bill_id = row.get("id") or row.get("invoice_id") or row.get("invoiceId")
        nested = row.get("payments") or row.get("payment_history") or row.get("paymentHistory") or row.get("transactions") or []
        if isinstance(nested, list) and nested:
            for index, payment in enumerate(nested):
                if isinstance(payment, dict):
                    payments.append(copy_row_scope({
                        **payment,
                        "id": payment.get("id") or f"{bill_id}-payment-{index}",
                        "invoice_id": bill_id,
                        "invoiceNumber": row.get("invoiceNumber") or row.get("invoice_number"),
                    }, row))
            continue
        payment_status = str(row.get("payment_status") or row.get("paymentStatus") or row.get("status") or "").lower()
        amount = row.get("paid_amount") or row.get("paidAmount") or row.get("amount_paid") or row.get("total") or row.get("amount")
        if payment_status in ["paid", "partial", "refunded", "failed", "reconciled"] or amount:
            payments.append(copy_row_scope({
                "id": f"{bill_id or external_id_for(row)}-payment",
                "title": row.get("invoiceNumber") or row.get("invoice_number") or row.get("title") or "POS Payment",
                "status": payment_status or "paid",
                "amount": amount or 0,
                "method": row.get("payment_method") or row.get("paymentMethod") or row.get("method") or "POS",
                "invoice_id": bill_id,
                "invoiceNumber": row.get("invoiceNumber") or row.get("invoice_number"),
                "customerName": row.get("customerName") or row.get("customer_name"),
                "customer_phone": row.get("customer_phone") or row.get("customerPhone"),
            }, row))
    return payments

def derive_customer_rows(rows: list) -> list:
    customers = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get('privacy_erased') or (row.get('metadata') or {}).get('privacy_erased'):
            continue
        name = row.get("customerName") or row.get("customer_name") or row.get("guest_name") or row.get("owner_name") or row.get("name")
        phone = row.get("customerPhone") or row.get("customer_phone") or row.get("phone") or row.get("contact")
        email = row.get("customerEmail") or row.get("customer_email") or row.get("email")
        if not any([name, phone, email]):
            continue
        key = (email or phone or name or external_id_for(row)).strip().lower()
        if key not in customers:
            customers[key] = copy_row_scope({
                "id": f"customer-{key}",
                "name": name or email or phone or "POS Customer",
                "email": email or "",
                "phone": phone or "",
                "status": "active",
                "order_count": 0,
                "total_spent": 0,
            }, row)
        customers[key]["order_count"] = int(customers[key].get("order_count") or 0) + 1
        customers[key]["total_spent"] = float(customers[key].get("total_spent") or 0) + float(row.get("total") or row.get("amount") or 0)
    return list(customers.values())

def derive_kitchen_rows(rows: list) -> list:
    tickets = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        items = row.get("items") or row.get("order_items") or row.get("orderItems") or []
        kitchen_status = row.get("kitchen_status") or row.get("kitchenStatus") or row.get("status")
        if isinstance(items, list) and items:
            for index, item in enumerate(items):
                ticket = item if isinstance(item, dict) else {"name": str(item)}
                tickets.append(copy_row_scope({
                    **ticket,
                    "id": ticket.get("id") or f"{external_id_for(row)}-kot-{index}",
                    "title": ticket.get("name") or ticket.get("title") or row.get("title") or "KOT Item",
                    "status": ticket.get("status") or kitchen_status or "pending",
                    "invoice_id": row.get("id") or row.get("invoice_id") or row.get("invoiceId"),
                    "table_number": row.get("table_number") or row.get("tableNumber"),
                }, row))
        elif kitchen_status:
            tickets.append(copy_row_scope({
                "id": f"{external_id_for(row)}-kot",
                "title": row.get("title") or row.get("invoiceNumber") or "KOT Ticket",
                "status": kitchen_status,
                "invoice_id": row.get("id") or row.get("invoice_id") or row.get("invoiceId"),
                "table_number": row.get("table_number") or row.get("tableNumber"),
            }, row))
    return tickets

async def prepare_pos_bridge_rows(resource: str, payload, business_id: Optional[str]) -> list:
    rows = normalize_pos_bridge_rows(payload)
    await validate_pos_rows_for_business(resource, rows, business_id)
    native_resource = payload.get("resource") if isinstance(payload, dict) else None
    expected_native = "kot" if resource == "kitchen-tickets" else resource
    if native_resource == expected_native or (rows and all(isinstance(row, dict) and row.get("sync_resource") == expected_native for row in rows)):
        return rows
    if resource == "payments":
        rows = derive_payment_rows(rows)
    elif resource == "customers":
        rows = derive_customer_rows(rows)
    elif resource == "kitchen-tickets":
        rows = derive_kitchen_rows(rows)
    return rows

def pos_row_business_id(row: dict) -> str:
    business = row.get("business")
    if isinstance(business, dict):
        business = business.get("id") or business.get("business_id") or business.get("businessId")
    return str(row.get("business_id") or row.get("businessId") or row.get("pos_business_id") or business or "").strip()

def pos_row_tenant_id(row: dict) -> str:
    tenant = row.get("tenant")
    if isinstance(tenant, dict):
        tenant = tenant.get("id") or tenant.get("tenant_id") or tenant.get("tenantId")
    return str(row.get("tenant_id") or row.get("tenantId") or row.get("pos_tenant_id") or tenant or "").strip()

async def expected_pos_scope_for_business(business_id: str) -> dict:
    business = await db.businesses.find_one(
        {"id": business_id},
        {"_id": 0, "id": 1, "name": 1, "pos_external_id": 1, "pos_tenant_id": 1},
    )
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    expected_business_id = str(business.get("pos_external_id") or business_id).strip()
    expected_tenant_id = str(business.get("pos_tenant_id") or f"admincore-{business_id}").strip()
    return {
        "local_business_id": business_id,
        "business_name": business.get("name", ""),
        "business_id": expected_business_id,
        "tenant_id": expected_tenant_id,
    }

def validate_pos_row_scope(resource: str, row: dict, scope: dict):
    actual_business_id = pos_row_business_id(row)
    actual_tenant_id = pos_row_tenant_id(row)
    if not actual_business_id or not actual_tenant_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POS_TENANT_SCOPE_MISSING",
                "resource": resource,
                "expected_business_id": scope["business_id"],
                "expected_tenant_id": scope["tenant_id"],
                "message": "POS returned data without business_id or tenant_id, so AdminCore refused to import it.",
            },
        )
    if actual_business_id != scope["business_id"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POS_TENANT_SCOPE_MISMATCH",
                "resource": resource,
                "expected_business_id": scope["business_id"],
                "actual_business_id": actual_business_id,
                "expected_tenant_id": scope["tenant_id"],
                "actual_tenant_id": actual_tenant_id,
                "message": "POS returned data for a different business, so AdminCore refused to import it.",
            },
        )
    if actual_tenant_id and scope["tenant_id"] and actual_tenant_id != scope["tenant_id"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POS_TENANT_SCOPE_MISMATCH",
                "resource": resource,
                "expected_business_id": scope["business_id"],
                "actual_business_id": actual_business_id,
                "expected_tenant_id": scope["tenant_id"],
                "actual_tenant_id": actual_tenant_id,
                "message": "POS returned data for a different tenant, so AdminCore refused to import it.",
            },
        )

async def validate_pos_rows_for_business(resource: str, rows: list, business_id: Optional[str]) -> list:
    if not business_id:
        return rows
    scope = await expected_pos_scope_for_business(business_id)
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(status_code=502, detail="POS returned a malformed record")
        validate_pos_row_scope(resource, row, scope)
    return rows

async def assert_pos_row_scope(resource: str, row: dict, business_id: Optional[str]):
    if not business_id or resource == "businesses":
        return
    scope = await expected_pos_scope_for_business(business_id)
    validate_pos_row_scope(resource, row, scope)

async def cleanup_mismatched_pos_imports(resource: str, business_id: Optional[str]):
    if not business_id or resource == "businesses":
        return
    config = pos_bridge_resource(resource)
    collection = db[config["collection"]]
    scope = await expected_pos_scope_for_business(business_id)
    quarantine_filter = {
        "business_id": business_id,
        "$or": [
            {"source": {"$in": ["pos", "pos-bridge"]}, "pos_business_id": {"$exists": False}},
            {"source": {"$in": ["pos", "pos-bridge"]}, "pos_business_id": ""},
            {"source": {"$in": ["pos", "pos-bridge"]}, "pos_business_id": {"$ne": scope["business_id"]}},
            {"pos_synced": True, "pos_business_id": {"$exists": False}},
            {"pos_synced": True, "pos_business_id": ""},
            {"pos_synced": True, "pos_business_id": {"$ne": scope["business_id"]}},
        ],
    }
    await collection.update_many(
        quarantine_filter,
        {
            "$set": {
                "tenant_scope_status": "quarantined",
                "tenant_scope_error": {
                    "code": "POS_TENANT_SCOPE_UNVERIFIED",
                    "expected_business_id": scope["business_id"],
                    "expected_tenant_id": scope["tenant_id"],
                },
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    if resource in ["staff", "staff-shifts"]:
        await db.users.update_many(
            {
                "business_ids": business_id,
                "pos_synced": True,
                "$or": [
                    {"pos_business_id": {"$exists": False}},
                    {"pos_business_id": ""},
                    {"pos_business_id": {"$ne": scope["business_id"]}},
                ],
            },
            {
                "$pull": {"business_ids": business_id},
                "$set": {
                    "tenant_scope_status": "quarantined",
                    "tenant_scope_error": {
                        "code": "POS_TENANT_SCOPE_UNVERIFIED",
                        "expected_business_id": scope["business_id"],
                        "expected_tenant_id": scope["tenant_id"],
                    },
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            },
        )

async def pos_bridge_request(resource: str, params: dict | None = None, business_id: Optional[str] = None):
    if not business_id:
        raise HTTPException(status_code=400, detail={"code": "POS_TENANT_SCOPE_MISSING", "message": "Select a business before syncing or viewing POS data."})
    ensure_pos_bridge_config()
    raise_if_pos_rate_limited(resource)
    config = pos_bridge_resource(resource)
    endpoint_candidates = config.get("endpoint_candidates") or [config["endpoint"]]
    headers = {}
    if POS_CORE_API_KEY:
        headers["Authorization"] = f"Bearer {POS_CORE_API_KEY}"
        headers["x-api-key"] = POS_CORE_API_KEY
    if business_id:
        headers.update(await pos_headers_for_admin_business(business_id))
    request_params = dict(params or {})
    request_params.setdefault("limit", 250)
    if not request_params.get('cursor') and not request_params.get('offset'):
        request_params.setdefault('consistent', 'true')
        request_params.setdefault('snapshot_request', str(uuid.uuid4()))
    if business_id and resource != "businesses":
        request_params.setdefault("business_id", headers.get("business_id") or headers.get("x-business-id"))
        request_params.setdefault("businessId", headers.get("business_id") or headers.get("x-business-id"))
        request_params.setdefault("tenant_id", headers.get("tenant_id") or headers.get("x-tenant-id"))
        request_params.setdefault("tenantId", headers.get("tenantId") or headers.get("x-tenant-id"))

    def do_request():
        session = requests.Session()
        last_response = None
        for index, endpoint in enumerate(endpoint_candidates):
            url = f"{POS_CORE_API_BASE_URL}/api/{endpoint.strip('/')}"
            response = send_pos_request(session, "GET", url, params=request_params, headers=headers, timeout=POS_CORE_REQUEST_TIMEOUT_SECONDS)
            response = await_export(response, lambda page_params: send_pos_request(session, 'GET', url,
                params=page_params, headers=headers, timeout=POS_CORE_REQUEST_TIMEOUT_SECONDS), request_params)
            last_response = response
            if response.status_code == 429:
                cooldown = mark_pos_rate_limited(response)
                raise HTTPException(status_code=429, detail={
                    "code": "POS_RATE_LIMITED",
                    "resource": resource,
                    "endpoint": endpoint,
                    "retry_after_seconds": cooldown,
                    "message": f"POS returned 429 Too Many Requests. AdminCore paused POS sync for {cooldown} seconds.",
                })
            if response.status_code == 404 and index < len(endpoint_candidates) - 1:
                continue
            if resource == "businesses" and response.status_code == 404:
                products_response = send_pos_request(session, "GET",
                    f"{POS_CORE_API_BASE_URL}/api/products",
                    params=request_params,
                    headers=headers,
                    timeout=POS_CORE_REQUEST_TIMEOUT_SECONDS,
                )
                products_response.raise_for_status()
                product_rows = normalize_pos_bridge_rows(products_response.json())
                businesses = {}
                for row in product_rows:
                    business_external_id = row.get("business_id") or row.get("businessId")
                    if business_external_id and business_external_id not in businesses:
                        businesses[business_external_id] = {
                            "id": business_external_id,
                            "business_id": business_external_id,
                            "tenantId": row.get("tenantId") or row.get("tenant_id"),
                            "tenant_id": row.get("tenantId") or row.get("tenant_id"),
                            "name": row.get("business_name") or row.get("businessName") or f"POS Business {str(business_external_id)[:8]}",
                            "type": "restaurant",
                            "plan": "starter",
                            "status": "active",
                        }
                return list(businesses.values())
            response.raise_for_status()
            payload = response.json()
            pagination = payload.get("meta", {}).get("pagination", {}) if isinstance(payload, dict) else {}
            if not pagination:
                return payload
            rows = normalize_pos_bridge_rows(payload)
            seen = set()
            def verify_page(page_rows):
                for row in page_rows:
                    identity = external_id_for(row)
                    if identity in seen:
                        raise HTTPException(502, {"code": "POS_PAGINATION_CHANGED", "message": "POS returned a repeated record during export. The snapshot was withheld; retry after source changes settle."})
                    seen.add(identity)
            verify_page(rows)
            previous_offset = int(pagination.get("offset", 0))
            started_at_zero = previous_offset == 0 and not request_params.get("cursor")
            strategy = pagination.get("strategy")
            complete_scope = pagination.get("complete_scope") is True
            pages = 1
            while pagination.get("has_more"):
                next_offset = pagination.get("next_offset")
                if not isinstance(next_offset, int) or next_offset <= previous_offset or pages >= 5000:
                    raise HTTPException(status_code=502, detail={"code": "POS_PAGINATION_INVALID", "message": "POS export pagination did not complete; no partial snapshot was imported."})
                next_params = {**request_params, "offset": next_offset, "limit": pagination["limit"]}
                if strategy in {"bounded-id-v1", "frozen-snapshot-v1"}:
                    cursor = pagination.get("next_cursor")
                    if not isinstance(cursor, str) or not cursor or len(cursor) > 8192:
                        raise HTTPException(502, "POS export omitted its stable continuation")
                    next_params["cursor"] = cursor
                next_response = send_pos_request(session, "GET", url, params=next_params, headers=headers, timeout=POS_CORE_REQUEST_TIMEOUT_SECONDS)
                next_response.raise_for_status()
                next_payload = next_response.json()
                if (next_payload.get("business_id") != payload.get("business_id") or next_payload.get("tenant_id") != payload.get("tenant_id")
                        or next_payload.get("resource") != payload.get("resource")):
                    raise HTTPException(status_code=409, detail={"code": "POS_TENANT_SCOPE_MISMATCH", "message": "POS changed business scope during pagination"})
                page_rows = normalize_pos_bridge_rows(next_payload)
                verify_page(page_rows)
                rows.extend(page_rows)
                pagination = next_payload.get("meta", {}).get("pagination")
                if not isinstance(pagination, dict):
                    raise HTTPException(status_code=502, detail="POS export omitted pagination metadata")
                if pagination.get("strategy") != strategy:
                    raise HTTPException(502, "POS changed export strategy during pagination")
                if strategy == 'frozen-snapshot-v1' and next_payload.get('meta', {}).get('snapshot') != payload.get('meta', {}).get('snapshot'):
                    raise HTTPException(502, 'POS changed its frozen export snapshot during pagination')
                complete_scope = complete_scope and pagination.get("complete_scope") is True
                if pagination.get("offset") != next_offset:
                    raise HTTPException(502, "POS export did not acknowledge the requested page offset")
                previous_offset = next_offset
                pages += 1
            return {**payload, "items": rows, "data": rows, "count": len(rows), "meta": {**payload.get("meta", {}), "pagination": {**pagination, "has_more": False, "complete_scope": complete_scope and started_at_zero}, "complete_snapshot": started_at_zero}}
        if last_response:
            last_response.raise_for_status()
        return []

    try:
        payload = await run_in_threadpool(do_request)
        await validate_pos_rows_for_business(resource, await prepare_pos_bridge_rows(resource, payload, business_id), business_id)
        return payload
    except HTTPException:
        raise
    except requests.RequestException as exc:
        detail = requests_error_detail(exc, f"POS bridge request for {resource}")
        detail.update({
            "code": "POS_BRIDGE_REQUEST_FAILED",
            "resource": resource,
            "endpoint": config.get("endpoint"),
            "tried": endpoint_candidates,
        })
        if detail.get("status_code") == 429:
            cooldown = mark_pos_rate_limited(getattr(exc, "response", None))
            raise HTTPException(status_code=429, detail={**detail, "code": "POS_RATE_LIMITED", "retry_after_seconds": cooldown}) from exc
        raise HTTPException(status_code=502, detail=detail) from exc

async def pos_core_session_request(method: str, endpoint: str, json: dict | None = None, params: dict | None = None, extra_headers: Optional[dict] = None, login_email: Optional[str] = None, login_password: Optional[str] = None, use_login: bool = True):
    ensure_pos_bridge_config()
    raise_if_pos_rate_limited(endpoint)
    headers = {}
    if POS_CORE_API_KEY:
        headers["Authorization"] = f"Bearer {POS_CORE_API_KEY}"
        headers["x-api-key"] = POS_CORE_API_KEY
    if extra_headers:
        headers.update(extra_headers)

    def do_request():
        session = requests.Session()
        if use_login:
            pos_core_login(session, headers, login_email, login_password)
        response = send_pos_request(session,
            method,
            f"{POS_CORE_API_BASE_URL}/api/{endpoint.strip('/')}",
            json=json,
            params=params or {},
            headers=headers,
            timeout=POS_CORE_REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 429:
            cooldown = mark_pos_rate_limited(response)
            raise HTTPException(status_code=429, detail={
                "code": "POS_RATE_LIMITED",
                "resource": endpoint,
                "retry_after_seconds": cooldown,
                "message": f"POS returned 429 Too Many Requests. AdminCore paused POS calls for {cooldown} seconds.",
            })
        response.raise_for_status()
        return response.json()

    try:
        return await run_in_threadpool(do_request)
    except HTTPException:
        raise
    except requests.RequestException as exc:
        detail = requests_error_detail(exc, f"POS core request for {endpoint}")
        if detail.get("status_code") == 429:
            cooldown = mark_pos_rate_limited(getattr(exc, "response", None))
            raise HTTPException(status_code=429, detail={**detail, "code": "POS_RATE_LIMITED", "retry_after_seconds": cooldown}) from exc
        raise HTTPException(status_code=502, detail=detail) from exc

async def pos_headers_for_admin_business(business_id: Optional[str]) -> dict:
    if not business_id:
        return {}
    business = await db.businesses.find_one(
        {"id": business_id},
        {"_id": 0, "id": 1, "pos_external_id": 1, "pos_tenant_id": 1},
    )
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    if not business.get("pos_external_id") or not business.get("pos_tenant_id"):
        raise HTTPException(status_code=409, detail={"code": "POS_TENANT_SCOPE_MISSING", "message": "Complete POS business provisioning before syncing records."})
    pos_business_id = str(business["pos_external_id"])
    tenant_id = str(business["pos_tenant_id"])
    return {
        "business_id": pos_business_id,
        "x-business-id": pos_business_id,
        "tenant_id": tenant_id,
        "tenantId": tenant_id,
        "x-tenant-id": tenant_id,
    }

SUBSCRIPTION_STATUSES = ["active", "trial", "trialing", "renewal", "past_due", "suspended", "expired", "cancelled"]
# Admin subscription status -> the state the POS enforces. Data is never deleted in any of them.
POS_SUBSCRIPTION_STATUS = {
    "active": "active", "renewal": "active", "trial": "trialing", "trialing": "trialing",
    "past_due": "past_due", "suspended": "suspended", "expired": "expired", "cancelled": "cancelled",
}

def effective_pos_status(business_status: Optional[str], subscription_status: Optional[str]) -> str:
    if business_status in ("suspended", "inactive"):
        return "suspended"
    return POS_SUBSCRIPTION_STATUS.get(subscription_status or "", "active")

async def push_pos_tenant_state(business_id: str, pos_status: str, period_end: Optional[str] = None):
    """Tell the customer application about a lifecycle change. No-op for businesses the POS has never seen."""
    if not POS_CORE_API_BASE_URL:
        return None
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0, "pos_external_id": 1, "pos_tenant_id": 1})
    if not business or not business.get("pos_external_id") or not business.get("pos_tenant_id"):
        return None
    headers = await pos_headers_for_admin_business(business_id)
    payload = {"subscription_status": pos_status, "billing_status": pos_status}
    if period_end:
        payload["current_period_end"] = period_end
    try:
        return await pos_core_session_request("PUT", f"admincore/tenants/{business['pos_external_id']}/subscription", json=payload, extra_headers=headers, use_login=False)
    except HTTPException as exc:
        raise HTTPException(status_code=502, detail={"code": "POS_TENANT_STATE_NOT_APPLIED", "message": "The POS did not accept the change, so nothing was saved. Retry when the POS is reachable.", "cause": compact_bridge_error_detail(exc.detail)}) from exc

async def push_billing_state_to_pos(business_id: str, new_subscription_status: str, period_end: Optional[str], plan_slug: Optional[str]):
    """Like push_subscription_status_to_pos, and also moves the POS to the plan that was paid for."""
    if not POS_CORE_API_BASE_URL:
        return None
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0, "status": 1, "pos_external_id": 1, "pos_tenant_id": 1}) or {}
    if not business.get("pos_external_id") or not business.get("pos_tenant_id"):
        return None
    headers = await pos_headers_for_admin_business(business_id)
    pos_status = effective_pos_status(business.get("status"), new_subscription_status)
    payload = {"subscription_status": pos_status, "billing_status": pos_status}
    if period_end:
        payload["current_period_end"] = period_end
    if plan_slug and saas_billing.PLAN_TO_POS.get(plan_slug):
        payload["plan"] = saas_billing.PLAN_TO_POS[plan_slug]
    try:
        return await pos_core_session_request("PUT", f"admincore/tenants/{business['pos_external_id']}/subscription", json=payload, extra_headers=headers, use_login=False)
    except HTTPException as exc:
        raise HTTPException(status_code=502, detail={"code": "POS_TENANT_STATE_NOT_APPLIED", "message": "The POS did not accept the change.", "cause": compact_bridge_error_detail(exc.detail)}) from exc

async def push_business_lifecycle_to_pos(business: dict, new_business_status: str):
    sub = await db.subscriptions.find_one({"business_id": business["id"]}, {"_id": 0, "status": 1, "current_period_end": 1})
    await push_pos_tenant_state(business["id"], effective_pos_status(new_business_status, (sub or {}).get("status")), (sub or {}).get("current_period_end"))

async def push_subscription_status_to_pos(business_id: str, new_subscription_status: str, period_end: Optional[str]):
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0, "status": 1}) or {}
    await push_pos_tenant_state(business_id, effective_pos_status(business.get("status"), new_subscription_status), period_end)

def admin_role_to_pos_role(role: str) -> str:
    if role == "business_owner":
        return "Owner"
    if role in ["manager", "support_admin", "platform_admin"]:
        return "Manager"
    return "Waiter"

async def is_pos_connected_business(business_id: str) -> bool:
    if not business_id or not POS_CORE_API_BASE_URL:
        return False
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0, "pos_external_id": 1, "pos_synced": 1, "pos_bridge_default": 1})
    return bool(business and (business.get("pos_external_id") or business.get("pos_synced") or business.get("pos_bridge_default")))


async def enqueue_pos_read_snapshot(resource: str, business_id: str) -> dict:
    """Read cached records without creating impossible jobs for partial links.

    Ownership checks still treat a partial link as POS-owned. This guard only
    controls automatic read refreshes; it must never enable local writes.
    """
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0, "pos_external_id": 1, "pos_tenant_id": 1})
    if not business:
        raise HTTPException(404, "Business not found")
    if not business.get("pos_external_id") or not business.get("pos_tenant_id"):
        return {"id": None, "status": "setup_required", "message": "Verify this business's POS connection in Businesses before refreshing records."}
    return await pos_sync_worker.enqueue_snapshot(resource, business_id, fresh_for_seconds=60)

async def ensure_default_outlet_for_business(
    business_id: str,
    user: Optional[dict] = None,
    sync_to_pos: bool = True,
    pos_business_id: Optional[str] = None,
    pos_tenant_id: Optional[str] = None,
) -> Optional[dict]:
    if not business_id:
        return None
    now_ts = datetime.now(timezone.utc).isoformat()
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
    if not business:
        return None
    resolved_pos_business_id = pos_business_id or business.get("pos_external_id") or business_id
    resolved_pos_tenant_id = pos_tenant_id or business.get("pos_tenant_id") or f"admincore-{business_id}"
    existing = await db.outlets.find_one({"business_id": business_id, "deleted_at": None}, {"_id": 0}, sort=[("created_at", 1)])
    if existing:
        verified_link = (
            existing.get("pos_synced") and existing.get("pos_external_id")
            and existing.get("pos_business_id") == resolved_pos_business_id
            and existing.get("pos_tenant_id") == resolved_pos_tenant_id
        )
        link_update = {
            "pos_business_id": resolved_pos_business_id,
            "pos_tenant_id": resolved_pos_tenant_id,
            "updated_at": now_ts,
        }
        await db.outlets.update_one({"id": existing["id"]}, {"$set": link_update})
        existing = {**existing, **link_update}
        if sync_to_pos and POS_CORE_API_BASE_URL and not verified_link:
            try:
                await push_admin_outlet_to_pos(existing, pos_headers={"business_id": resolved_pos_business_id, "x-tenant-id": resolved_pos_tenant_id})
                existing = await db.outlets.find_one({"id": existing["id"]}, {"_id": 0})
            except HTTPException as exc:
                logger.warning("Could not sync existing default outlet to POS: %s", exc.detail)
                raise
        return existing

    outlet_doc = {
        "id": str(ObjectId()),
        "business_id": business_id,
        "name": "Main Outlet",
        "code": make_outlet_code("MAIN"),
        "address": business.get("address") or "",
        "manager_name": "",
        "phone": business.get("phone") or "",
        "status": "active",
        "source": "admincore-default",
        "pos_business_id": resolved_pos_business_id,
        "pos_tenant_id": resolved_pos_tenant_id,
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    outlet_doc, created = await create_default_outlet_once(db.outlets, outlet_doc)
    if user and created:
        await create_audit_log(business_id, user["id"], user["email"], "created", "outlet", outlet_doc["id"], {"name": "Main Outlet", "default": True})
    if sync_to_pos and POS_CORE_API_BASE_URL:
        try:
            await push_admin_outlet_to_pos(outlet_doc, pos_headers={"business_id": resolved_pos_business_id, "x-tenant-id": resolved_pos_tenant_id})
            outlet_doc = await db.outlets.find_one({"id": outlet_doc["id"]}, {"_id": 0})
        except HTTPException as exc:
            logger.warning("Could not sync default outlet to POS: %s", exc.detail)
            raise
    return {k: v for k, v in outlet_doc.items() if k != "_id"}

async def provision_admin_business_to_pos(business_id: str) -> Optional[dict]:
    if not POS_CORE_API_BASE_URL or not business_id:
        return None
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
    if not business:
        return None
    pos_business_id = business.get("pos_external_id") or business["id"]
    tenant_id = business.get("pos_tenant_id") or f"admincore-{business['id']}"
    payload = {
        "id": pos_business_id,
        "business_id": pos_business_id,
        "tenantId": tenant_id,
        "tenant_id": tenant_id,
        "name": business.get("name") or "AdminCore Business",
        "type": business.get("type") or "restaurant",
        "plan": business.get("plan") or "starter",
        "status": business.get("status") or "active",
    }
    # Both applications have different plan catalogs. Send the actual grants,
    # rather than letting POS silently substitute its own starter defaults.
    payload.update(pos_entitlement_payload(await get_business_entitlements(business_id)))
    result = await pos_core_session_request("POST", "admincore/tenants", json=payload, use_login=False)
    created = result.get("data", result) if isinstance(result, dict) else None
    created_business = created.get("business", created) if isinstance(created, dict) else None
    if not isinstance(created_business, dict):
        raise HTTPException(status_code=502, detail="POS provisioning returned no business identity")
    returned_id = created_business.get("id") or created_business.get("business_id")
    returned_tenant = created_business.get("tenantId") or created_business.get("tenant_id")
    if str(returned_id or "") != str(pos_business_id) or str(returned_tenant or "") != str(tenant_id):
        raise HTTPException(status_code=502, detail="POS provisioning returned a different business or tenant identity")
    await db.businesses.update_one(
        {"id": business_id},
        {"$set": {"pos_external_id": pos_business_id, "pos_tenant_id": tenant_id, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    await ensure_default_outlet_for_business(business_id, sync_to_pos=False, pos_business_id=pos_business_id, pos_tenant_id=tenant_id)
    return {"business_id": pos_business_id, "tenant_id": tenant_id, "result": result}

async def verify_pos_actor_contract(headers):
    if not headers.get('x-admincore-actor-mode'):
        return
    result = await pos_core_session_request('GET', 'admincore/capabilities', extra_headers=headers, use_login=False)
    capabilities = result.get('data', result) if isinstance(result, dict) else {}
    if capabilities.get('actor_checks') != 1:
        raise HTTPException(503, 'Upgrade the POS bridge before delivering queued identity-scoped changes')


async def push_admin_user_to_pos(user_doc: dict, password: Optional[str] = None, allow_generated_password: bool = False, target_business_id: Optional[str] = None, previous_email: Optional[str] = None, changed_fields=None, command_id=None):
    business_ids = user_doc.get("business_ids") or []
    if not POS_CORE_API_BASE_URL or not business_ids:
        return None
    if not user_doc.get("email"):
        return None
    business_id = target_business_id or business_ids[0]
    if business_id not in business_ids:
        raise HTTPException(status_code=403, detail="User is not assigned to the requested POS business")
    pos_headers = await pos_headers_for_admin_business(business_id)
    pos_headers.update(user_doc.get('_pos_actor_headers') or {})
    await verify_pos_actor_contract(pos_headers)
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
    pos_business_id = (business or {}).get("pos_external_id") or pos_headers.get("business_id") or business_id
    pos_tenant_id = (business or {}).get("pos_tenant_id") or pos_headers.get("x-tenant-id") or f"admincore-{business_id}"
    pos_outlet_id = None
    pos_link = (user_doc.get("pos_links") or {}).get(business_id) or {}
    saved_command = None
    if command_id:
        job = await db.pos_profile_jobs.find_one({'_id': user_doc['id'], 'event.operation_id': command_id}, {'event.target_commands': 1})
        saved_command = ((job or {}).get('event', {}).get('target_commands') or {}).get(business_id)
    existing_pos_user_id = pos_link.get("user_id") or (
        user_doc.get("pos_external_id") if user_doc.get("pos_business_id") == pos_business_id else None
    )
    if not saved_command and not existing_pos_user_id and previous_email:
        lookup_email = user_doc['email'] if user_doc.get('pos_creation_pending') else previous_email
        exported = await pos_bridge_request("staff", {}, business_id=business_id)
        rows = await prepare_pos_bridge_rows("staff", exported, business_id)
        matches = [row for row in rows if (row.get("email") or "").strip().lower() == lookup_email.strip().lower()]
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="Multiple POS accounts match this user in the business; repair the user link before updating")
        if matches:
            if user_doc.get('pos_creation_pending'):
                raise HTTPException(409, {'status_code': 409, 'response': {'error': {'code': 'EMAIL_EXISTS', 'message': 'This email already belongs to a POS account. Import or review that account instead of creating another identity.'}}})
            existing_pos_user_id = matches[0].get("id")
            if not existing_pos_user_id:
                raise HTTPException(status_code=502, detail="POS staff export omitted the user ID")
            # Persist the verified identity before an email change can hide it from retries.
            await db.users.update_one({"id": user_doc["id"]}, {"$set": {
                f"pos_links.{business_id}.user_id": existing_pos_user_id,
                f"pos_links.{business_id}.business_id": pos_business_id,
                f"pos_links.{business_id}.tenant_id": pos_tenant_id,
            }})
    payload = {
        "name": user_doc.get("name") or user_doc["email"].split("@")[0],
        "email": user_doc["email"],
        "role": admin_role_to_pos_role(user_doc.get("role")),
        "active": user_doc.get("status", "active") == "active",
        "profile_required": False,
        "business_id": pos_business_id,
        "businessId": pos_business_id,
        "tenant_id": pos_tenant_id,
        "tenantId": pos_tenant_id,
        "assigned_outlet_ids": [pos_outlet_id] if pos_outlet_id else [],
        "assignedOutletIds": [pos_outlet_id] if pos_outlet_id else [],
        "default_outlet_id": pos_outlet_id or "",
        "defaultOutletId": pos_outlet_id or "",
    }
    if existing_pos_user_id:
        fields = set(changed_fields) if changed_fields is not None else {'name', 'email', 'role', 'status'}
        for field, source_field in [('name', 'name'), ('email', 'email'), ('role', 'role'), ('active', 'status')]:
            if source_field not in fields:
                payload.pop(field, None)
        for field in ['profile_required', 'assigned_outlet_ids', 'assignedOutletIds', 'default_outlet_id', 'defaultOutletId']:
            payload.pop(field, None)
        if 'role' in fields:
            payload['reset_permissions_for_role'] = True
    elif not saved_command:
        outlet = await ensure_default_outlet_for_business(business_id, sync_to_pos=True,
            pos_business_id=pos_business_id, pos_tenant_id=pos_tenant_id)
        pos_outlet_id = (outlet or {}).get('pos_external_id') or (outlet or {}).get('id')
        payload.update(assigned_outlet_ids=[pos_outlet_id] if pos_outlet_id else [],
                       assignedOutletIds=[pos_outlet_id] if pos_outlet_id else [],
                       default_outlet_id=pos_outlet_id or '', defaultOutletId=pos_outlet_id or '')
    if password:
        payload["password"] = password
    elif not existing_pos_user_id:
        if not allow_generated_password:
            raise HTTPException(status_code=400, detail="Set a new password before syncing this user to POS")
        payload["password"] = secrets.token_urlsafe(10)
    if command_id:
        payload['admincore_command_id'] = command_id

    method = "PUT" if existing_pos_user_id else "POST"
    endpoint = f"admincore/staff/{existing_pos_user_id}" if existing_pos_user_id else "admincore/staff"
    if command_id:
        if not saved_command:
            candidate = {'method': method, 'endpoint': endpoint, 'payload': payload}
            row = await db.pos_profile_jobs.find_one_and_update(
                {'_id': user_doc['id'], 'event.operation_id': command_id, f'event.target_commands.{business_id}': {'$exists': False}},
                {'$set': {f'event.target_commands.{business_id}': candidate}}, return_document=ReturnDocument.AFTER)
            if row:
                saved_command = row['event']['target_commands'][business_id]
            else:
                row = await db.pos_profile_jobs.find_one({'_id': user_doc['id'], 'event.operation_id': command_id})
                saved_command = ((row or {}).get('event', {}).get('target_commands') or {}).get(business_id)
            if not saved_command:
                raise HTTPException(409, 'The profile command changed before POS dispatch; review its current status')
        method, endpoint, payload = saved_command['method'], saved_command['endpoint'], saved_command['payload']
        if payload.get('business_id') != pos_business_id or payload.get('tenant_id') != pos_tenant_id:
            raise HTTPException(409, 'The saved profile command no longer matches this business mapping')
    result = await pos_core_session_request(method, endpoint, json=payload, extra_headers=pos_headers, use_login=False)
    created = result.get("data") if isinstance(result, dict) and "data" in result else result
    pos_user_id = created.get("id") if isinstance(created, dict) else None
    created_business_id = str((created or {}).get("business_id") or (created or {}).get("businessId") or "")
    created_tenant_id = str((created or {}).get("tenant_id") or (created or {}).get("tenantId") or "")
    if not pos_user_id or created_business_id != str(pos_business_id) or created_tenant_id != str(pos_tenant_id):
        raise HTTPException(status_code=502, detail={
            "code": "POS_USER_BUSINESS_MISMATCH",
            "message": "POS linked the owner user under a different business than AdminCore requested.",
            "email": user_doc["email"],
            "created_business_id": created_business_id,
            "target_business_id": pos_business_id,
        })
    if pos_user_id:
        link_update = {
            f'pos_links.{business_id}.user_id': pos_user_id,
            f'pos_links.{business_id}.business_id': pos_business_id,
            f'pos_links.{business_id}.tenant_id': pos_tenant_id,
        }
        if isinstance(created, dict):
            for field, source in [('role', 'role'), ('permissions', 'permissions'), ('outlet_ids', 'assigned_outlet_ids')]:
                if source in created:
                    link_update[f'pos_links.{business_id}.{field}'] = normalize_pos_staff_role(created[source]) if field == 'role' else created[source]
            if 'active' in created:
                link_update[f'pos_links.{business_id}.status'] = 'active' if created['active'] else 'inactive'
        await db.users.update_one(
            {"id": user_doc["id"]},
            {"$set": {
                "pos_external_id": pos_user_id,
                "pos_business_id": pos_business_id,
                "pos_tenant_id": pos_tenant_id,
                **link_update,
                "pos_synced": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
    return result

async def push_admin_product_to_pos(product_doc: dict):
    business_id = product_doc.get("business_id")
    if not POS_CORE_API_BASE_URL or not business_id:
        return None
    pos_headers = await pos_headers_for_admin_business(business_id)
    pos_headers.update(product_doc.get('_pos_actor_headers') or {})
    await verify_pos_actor_contract(pos_headers)
    pos_business_id = pos_headers.get("business_id") or pos_headers.get("x-business-id") or business_id
    pos_tenant_id = pos_headers.get("tenant_id") or pos_headers.get("x-tenant-id") or f"admincore-{business_id}"
    linked = (product_doc.get("pos_external_id")
              and product_doc.get("pos_business_id") == pos_business_id
              and product_doc.get("pos_tenant_id") == pos_tenant_id)
    if product_doc.get("pos_external_id") and not linked:
        raise HTTPException(409, {"code": "POS_PRODUCT_SCOPE_MISMATCH", "message": "The saved product link does not match this business"})
    existing = {"id": product_doc["pos_external_id"]} if linked else None
    command = product_doc.get("pos_command") or {}
    if command:
        try:
            capabilities = await pos_core_session_request("GET", "admincore/capabilities", extra_headers=pos_headers, use_login=False)
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, dict) else {}
            if detail.get("status_code", error.status_code) != 404:
                raise
            raise HTTPException(409, {"status_code": 409, "code": "POS_PRODUCT_CONTRACT_REQUIRED", "message": "The POS bridge must be upgraded before accepting this product command"}) from None
        capabilities = capabilities.get("data", capabilities) if isinstance(capabilities, dict) else {}
        if capabilities.get("product_commands") != 1:
            raise HTTPException(409, {"status_code": 409, "code": "POS_PRODUCT_CONTRACT_REQUIRED", "message": "The POS bridge must be upgraded before accepting this product command"})
    payload = {
        "name": product_doc.get("name") or "Product",
        "price": product_doc.get("price") or 0,
        "stock": product_doc.get("stock") or 0,
        "category": product_doc.get("category") or "General",
        "active": product_doc.get("active", True),
        "admincore_product_id": "acp_" + hashlib.sha256(f"{business_id}:{product_doc['id']}".encode()).hexdigest()[:32],
        "admincore_command_id": command.get("id"),
        "expected_updated_at": command.get("source_updated_at"),
        "business_id": pos_business_id,
        "businessId": pos_business_id,
        "tenant_id": pos_tenant_id,
        "tenantId": pos_tenant_id,
    }
    if existing:
        result = await pos_core_session_request("PUT", f"admincore/products/{existing['id']}", json=payload, extra_headers=pos_headers, use_login=False)
        pos_product_id = existing["id"]
    else:
        result = await pos_core_session_request("POST", "admincore/products", json=payload, extra_headers=pos_headers, use_login=False)
        created = result.get("data") if isinstance(result, dict) and "data" in result else result
        pos_product_id = created.get("id") if isinstance(created, dict) else None
    created = result.get("data", result) if isinstance(result, dict) else None
    if (not isinstance(created, dict) or not pos_product_id or created.get("id") != pos_product_id
            or (command and not existing and pos_product_id != payload["admincore_product_id"])
            or str(created.get("business_id") or created.get("businessId") or "") != str(pos_business_id)
            or str(created.get("tenant_id") or created.get("tenantId") or "") != str(pos_tenant_id)):
        raise HTTPException(status_code=502, detail={"code": "POS_PRODUCT_SCOPE_MISMATCH", "message": "POS returned an unverified product identity"})
    if pos_product_id:
        await db.products.update_one(
            {"id": product_doc["id"]},
            {"$set": {"pos_external_id": pos_product_id, "pos_business_id": pos_business_id, "pos_tenant_id": pos_tenant_id, "pos_synced": True, "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    return result

async def delete_admin_product_from_pos(product_doc: dict):
    if not POS_CORE_API_BASE_URL or not product_doc.get("business_id") or not product_doc.get("pos_external_id"):
        return None
    pos_headers = await pos_headers_for_admin_business(product_doc["business_id"])
    pos_headers.update(product_doc.get('_pos_actor_headers') or {})
    await verify_pos_actor_contract(pos_headers)
    if product_doc.get("pos_business_id") != pos_headers.get("business_id") or product_doc.get("pos_tenant_id") != pos_headers.get("x-tenant-id"):
        raise HTTPException(409, {"status_code": 409, "message": "Product source scope does not match the selected business"})
    return await pos_core_session_request("DELETE", f"admincore/products/{product_doc['pos_external_id']}", json={"expected_updated_at": (product_doc.get("pos_command") or {}).get("source_updated_at")}, extra_headers=pos_headers, use_login=False)

async def push_admin_outlet_to_pos(outlet_doc: dict, pos_headers: Optional[dict] = None):
    business_id = outlet_doc.get("business_id")
    if not POS_CORE_API_BASE_URL or not business_id:
        return None
    pos_headers = pos_headers or await pos_headers_for_admin_business(business_id)
    pos_headers = {**pos_headers, **(outlet_doc.get('_pos_actor_headers') or {})}
    await verify_pos_actor_contract(pos_headers)
    linked = (outlet_doc.get("pos_external_id")
              and outlet_doc.get("pos_business_id") == pos_headers.get("business_id")
              and outlet_doc.get("pos_tenant_id") == pos_headers.get("x-tenant-id"))
    if outlet_doc.get("pos_external_id") and not linked:
        raise HTTPException(409, {"status_code": 409, "message": "Outlet source scope does not match the selected business"})
    existing = {"id": outlet_doc["pos_external_id"]} if linked else None
    command = outlet_doc.get("pos_command") or {}
    if command:
        try:
            capabilities = await pos_core_session_request("GET", "admincore/capabilities", extra_headers=pos_headers, use_login=False)
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, dict) else {}
            if detail.get("status_code", error.status_code) != 404:
                raise
            capabilities = {}
        capabilities = capabilities.get("data", capabilities) if isinstance(capabilities, dict) else {}
        if capabilities.get("outlet_commands") != 1:
            raise HTTPException(409, {"status_code": 409, "code": "POS_OUTLET_CONTRACT_REQUIRED", "message": "Upgrade the POS bridge before accepting this outlet command"})
    payload = {
        "name": outlet_doc.get("name") or "Outlet",
        "admincore_outlet_id": "aco_" + hashlib.sha256(f"{business_id}:{outlet_doc['id']}".encode()).hexdigest()[:32],
        "admincore_command_id": command.get("id"),
        "expected_updated_at": command.get("source_updated_at"),
        "code": outlet_doc.get("code") or make_outlet_code("OUT"),
        "location": outlet_doc.get("address") or "",
        "address": outlet_doc.get("address") or "",
        "manager_name": outlet_doc.get("manager_name") or "",
        "managerName": outlet_doc.get("manager_name") or "",
        "phone": outlet_doc.get("phone") or "",
        "status": outlet_doc.get("status") or "active",
        "business_id": pos_headers.get("business_id") or business_id,
        "tenantId": pos_headers.get("x-tenant-id") or "",
        "tenant_id": pos_headers.get("x-tenant-id") or "",
    }
    if existing:
        result = await pos_core_session_request("PUT", f"admincore/outlets/{existing['id']}", json=payload, extra_headers=pos_headers, use_login=False)
        pos_outlet_id = existing["id"]
    else:
        result = await pos_core_session_request("POST", "admincore/outlets", json=payload, extra_headers=pos_headers, use_login=False)
        created = result.get("data") if isinstance(result, dict) and "data" in result else result
        pos_outlet_id = created.get("id") if isinstance(created, dict) else None
    created = result.get("data", result) if isinstance(result, dict) else None
    if not isinstance(created, dict) or not pos_outlet_id or (command and not existing and pos_outlet_id != payload["admincore_outlet_id"]) or created.get("id") != pos_outlet_id or str(created.get("business_id") or created.get("businessId") or "") != str(payload["business_id"]) or str(created.get("tenant_id") or created.get("tenantId") or "") != str(payload["tenant_id"]):
        raise HTTPException(status_code=502, detail={"code": "POS_OUTLET_SCOPE_MISMATCH", "message": "POS returned an unverified default outlet identity"})
    if pos_outlet_id:
        await db.outlets.update_one(
            {"id": outlet_doc["id"]},
            {"$set": {
                "pos_external_id": pos_outlet_id,
                "pos_business_id": payload["business_id"],
                "pos_tenant_id": payload["tenant_id"],
                "pos_synced": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
    return result

async def delete_admin_outlet_from_pos(outlet_doc: dict):
    if not POS_CORE_API_BASE_URL or not outlet_doc.get("business_id"):
        return None
    # POS retains inactive outlets so historical sales, menus and stock references survive.
    return await push_admin_outlet_to_pos({**outlet_doc, "status": "inactive"})


def external_id_for(row: dict) -> str:
    return str(row.get("id") or row.get("_id") or row.get("external_id") or row.get("code") or row.get("trackingToken") or ObjectId())

def title_for(row: dict, fallback: str) -> str:
    return str(row.get("title") or row.get("name") or row.get("customerName") or row.get("invoiceNumber") or row.get("trackingToken") or fallback)

def bridge_error_message(error) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("detail") or error.get("code") or error)
    return str(error)

def require_pos_bridge_sync_key(request: Request):
    configured_keys = [key for key in [ADMINCORE_API_KEY, POS_CORE_API_KEY] if key]
    if not configured_keys:
        raise HTTPException(status_code=503, detail="POS bridge webhook key is not configured")

    candidate = request.headers.get("x-admincore-api-key") or request.headers.get("x-api-key") or ""
    auth_header = request.headers.get("authorization") or ""
    if not candidate and auth_header.lower().startswith("bearer "):
        candidate = auth_header[7:]

    # Compare against every configured key without short-circuiting on the first mismatch.
    matches = [hmac.compare_digest(candidate.encode("utf-8"), key.encode("utf-8")) for key in configured_keys]
    if not candidate or not any(matches):
        raise HTTPException(status_code=401, detail="Invalid POS bridge webhook key")

async def get_pos_bridge_system_user() -> dict:
    user = await db.users.find_one({"role": "platform_admin"}, {"_id": 0}, sort=[("created_at", 1)])
    if user:
        return user

    user = await db.users.find_one({}, {"_id": 0}, sort=[("created_at", 1)])
    if user:
        return user

    return {
        "id": "pos-bridge-system",
        "email": "pos-bridge@admincore.local",
        "name": "POS Bridge",
        "role": "platform_admin",
        "business_ids": [],
    }

async def record_pos_bridge_sync_run(
    resource: str,
    business_id: Optional[str],
    user: dict,
    status: str,
    count: int,
    error_count: int,
    errors: list[dict],
    started_at: str,
) -> dict:
    now_ts = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(ObjectId()),
        "resource": resource,
        "business_id": business_id or "",
        "status": status,
        "synced_count": count,
        "error_count": error_count,
        "errors": errors[:50],
        "started_at": started_at,
        "finished_at": now_ts,
        "created_by": user["id"],
        "created_at": now_ts,
    }
    await db.pos_bridge_sync_runs.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}

async def sync_bridge_business(row: dict, user: dict, now_ts: str) -> str:
    external_id = external_id_for(row)
    existing = await db.businesses.find_one({"pos_external_id": external_id}, {"_id": 0})
    incoming_tenant = row.get("tenantId") or row.get("tenant_id")
    if existing and incoming_tenant and existing.get("pos_tenant_id") and incoming_tenant != existing["pos_tenant_id"]:
        raise HTTPException(status_code=409, detail="POS business tenant identity changed; review the mapping before importing")
    source_version = row.get('updated_at')
    if existing and source_version and existing.get('pos_source_updated_at'):
        if datetime.fromisoformat(source_version.replace('Z', '+00:00')) < datetime.fromisoformat(existing['pos_source_updated_at'].replace('Z', '+00:00')):
            return existing['id']
    name = title_for(row, "POS Business")
    doc = {
        "name": name,
        "slug": existing.get("slug") if existing else await unique_business_slug(row.get("slug") or name),
        "type": (existing or {}).get('type') or row.get("type") or "restaurant",
        "plan": (existing or {}).get("plan") or row.get("plan") or "starter",
        "status": (existing or {}).get("status") or row.get("status") or "active",
        "branding": {"primary_color": "#0055FF", **((existing or {}).get('branding') or {}), **(row.get('branding') or {}), 'business_name': name},
        "pos_external_id": external_id,
        "pos_tenant_id": row.get("tenantId") or row.get("tenant_id") or (existing or {}).get("pos_tenant_id"),
        "pos_synced": True,
        "updated_at": now_ts,
    }
    if source_version:
        doc['pos_source_updated_at'] = source_version
    profile = row.get('profile')
    if isinstance(profile, dict):
        if profile.get('business_id') != external_id or profile.get('tenant_id') != incoming_tenant:
            raise HTTPException(409, 'POS business profile identity does not match its export')
        fields = {'shopName', 'gst', 'address', 'phone', 'taxRate', 'footer', 'receiptOfferTitle', 'receiptOfferMessage', 'currency', 'paymentMethods', 'business_id', 'tenant_id', 'updated_at', 'persisted'}
        doc['pos_profile'] = {key: value for key, value in profile.items() if key in fields}
    if existing:
        await db.businesses.update_one({"id": existing["id"], 'pos_source_updated_at': existing.get('pos_source_updated_at')}, {"$set": doc})
        return existing["id"]
    doc.update({"id": str(ObjectId()), "owner_id": user["id"], "created_at": now_ts})
    await db.businesses.insert_one(doc)
    if not is_platform_staff(user):
        await db.users.update_one({"id": user["id"]}, {"$addToSet": {"business_ids": doc["id"]}})
    await seed_business_defaults(doc["id"])
    # Imported outlets must retain their actual POS identities. The outlet import
    # will populate them; creating a synthetic default here disconnects history.
    return doc["id"]

async def local_business_id_for_pos_row(row: dict, user: dict, now_ts: str) -> Optional[str]:
    pos_business_id = row.get("business_id") or row.get("businessId")
    if not pos_business_id:
        return None
    existing = await db.businesses.find_one({"pos_external_id": str(pos_business_id)}, {"_id": 0, "id": 1})
    if existing:
        return existing["id"]
    return await sync_bridge_business(
        {
            "id": str(pos_business_id),
            "name": row.get("business_name") or row.get("businessName") or f"POS Business {str(pos_business_id)[:8]}",
            "tenantId": row.get("tenantId") or row.get("tenant_id"),
            "status": "active",
            "type": "restaurant",
            "plan": "starter",
        },
        user,
        now_ts,
    )

async def sync_bridge_outlet(row: dict, business_id: str, now_ts: str) -> str:
    await assert_pos_row_scope("outlets", row, business_id)
    external_id = external_id_for(row)
    outlet_name = title_for(row, "POS Outlet")
    outlet_code = row.get("code")
    # Names/codes are editable and need not be unique. They are not identities.
    match_terms = [{"pos_external_id": external_id}, {"external_id": external_id}]
    existing = await db.outlets.find_one(
        {
            "business_id": business_id,
            "source_alias_to": None,
            "$or": match_terms,
        },
        {"_id": 0},
    )
    if existing and existing.get("deleted_at"):
        return existing["id"]
    doc = {
        "business_id": business_id,
        "name": outlet_name,
        "code": row.get("code") or (existing.get("code") if existing else make_outlet_code("POS")),
        "address": row.get("address") or row.get("location") or "",
        "manager_name": row.get("manager_name") or row.get("managerName") or row.get("manager") or "",
        "phone": row.get("phone") or "",
        "status": row.get("status") or "active",
        "source": row.get("source") or "pos",
        "pos_business_id": row.get("business_id") or row.get("businessId") or "",
        "pos_tenant_id": row.get("tenantId") or row.get("tenant_id") or "",
        "pos_external_id": external_id,
        "pos_synced": True,
        "pos_metadata": {key: row[key] for key in ("id", "updated_at", "name", "code", "status", "location", "manager_name", "phone") if key in row},
        "source_present": True,
        "tenant_scope_status": "verified",
        "updated_at": now_ts,
    }
    if existing:
        await db.outlets.update_one({"id": existing["id"]}, {"$set": doc})
        return existing["id"]
    doc.update({"id": str(ObjectId()), "created_at": now_ts})
    await db.outlets.insert_one(doc)
    return doc["id"]

async def sync_bridge_product(row: dict, business_id: str, now_ts: str) -> str:
    await assert_pos_row_scope("products", row, business_id)
    external_id = external_id_for(row)
    product_name = title_for(row, "POS Product")
    existing = await db.products.find_one(
        {
            "business_id": business_id,
            "source_alias_to": None,
            "$or": [
                {"pos_external_id": external_id},
                {"external_id": external_id},
            ],
        },
        {"_id": 0},
    )
    doc = {
        "name": product_name,
        "price": float(row.get("price") or 0),
        "stock": int(row.get("stock") or 0),
        "category": row.get("category") or "POS Imported",
        "business_id": business_id,
        "outlet_id": row.get("outlet_id") or row.get("outletId") or "",
        "active": row.get("active", True) is not False,
        "source": "pos",
        "external_id": external_id,
        "pos_metadata": row,
        "pos_external_id": external_id,
        "pos_business_id": row.get("business_id") or row.get("businessId") or "",
        "pos_tenant_id": row.get("tenantId") or row.get("tenant_id") or "",
        "pos_synced": True,
        "source_present": True,
        "tenant_scope_status": "verified",
        "updated_at": now_ts,
    }
    if existing:
        await db.products.update_one({"id": existing["id"]}, {"$set": doc})
        return existing["id"]
    doc.update({"id": str(ObjectId()), "created_at": now_ts})
    await db.products.insert_one(doc)
    return doc["id"]

def normalize_pos_staff_role(role: str) -> str:
    normalized = (role or "staff").strip().lower().replace(" ", "_")
    if normalized in ["owner", "business_owner"]:
        return "business_owner"
    if normalized in ["manager", "admin"]:
        return "manager"
    if normalized in ["platform_admin", "support_admin"]:
        return "support_admin"
    return "staff"

def known_pos_staff_password(email: str) -> str:
    normalized = (email or "").strip().lower()
    if normalized == "owner@pos.com":
        return "admin123"
    if normalized == "cashier@pos.com":
        return "cash123"
    for prefix, base in [("manager", "manager"), ("chef", "chef"), ("waiter", "waiter")]:
        if normalized.startswith(prefix) and normalized.endswith("@pos.com"):
            number = normalized.removeprefix(prefix).removesuffix("@pos.com")
            if number.isdigit():
                return f"{base}{100 + int(number)}"
    if normalized.endswith("@test.com"):
        return "testpass123"
    return secrets.token_urlsafe(18)

async def sync_bridge_staff_user(row: dict, business_id: str, now_ts: str) -> Optional[str]:
    await assert_pos_row_scope("staff", row, business_id)
    email = (row.get("email") or "").strip().lower()
    if not email:
        return None
    external_id = external_id_for(row)
    existing = await db.users.find_one({"business_ids": business_id, "$or": [
        {f"pos_links.{business_id}.user_id": external_id},
        {"pos_external_id": external_id, "pos_business_id": pos_row_business_id(row)},
    ]}, {"_id": 0})
    email_owner = await db.users.find_one({"email": email}, {"_id": 0})
    if email_owner and (not existing or email_owner["id"] != existing["id"]):
        if email_owner.get('pos_creation_pending'):
            raise HTTPException(409, 'A pending AdminCore account cannot claim an existing POS identity by email')
        if existing or business_id not in email_owner.get("business_ids", []) or email_owner.get("role") in {"platform_admin", "support_admin"}:
            raise HTTPException(status_code=409, detail="POS staff email conflicts with another AdminCore account")
        existing = email_owner
    if existing and existing.get("role") in PLATFORM_ROLES | {"support_admin"}:
        raise HTTPException(status_code=409, detail="POS staff sync cannot modify a platform account")
    role = normalize_pos_staff_role(row.get("role"))
    status = "active" if row.get("active", True) is not False and str(row.get("status", "active")).lower() != "inactive" else "inactive"
    update = {
        "email": email,
        f"pos_links.{business_id}": {"user_id": external_id, "business_id": pos_row_business_id(row), "tenant_id": pos_row_tenant_id(row),
            "name": row.get("name") or row.get("staffName") or email.split("@")[0], "email": email,
            "role": role, "status": status, "permissions": row.get("permissions") or [],
            "last_seen_at": now_ts, "source_present": True,
            "outlet_ids": row.get("assigned_outlet_ids") or row.get("assignedOutletIds") or []},
        "name": row.get("name") or row.get("staffName") or email.split("@")[0],
        "role": role,
        "status": status,
        "pos_external_id": external_id_for(row),
        "pos_business_id": row.get("business_id") or row.get("businessId") or "",
        "pos_tenant_id": row.get("tenantId") or row.get("tenant_id") or "",
        "pos_synced": True,
        "tenant_scope_status": "verified",
        "pos_permissions": row.get("permissions") or [],
        "pos_assigned_outlet_ids": row.get("assigned_outlet_ids") or row.get("assignedOutletIds") or [],
        "updated_at": now_ts,
    }
    if existing:
        if set(existing.get("business_ids") or []) - {business_id}:
            for field in ("role", "status", "name", "email", "pos_permissions", "pos_assigned_outlet_ids", "pos_external_id", "pos_business_id", "pos_tenant_id"):
                update.pop(field, None)
        await db.users.update_one({"id": existing["id"]}, {"$set": update, "$addToSet": {"business_ids": business_id}})
        return existing["id"]
    doc = {
        "id": str(ObjectId()),
        "email": email,
        "password_hash": hash_password(secrets.token_urlsafe(32)),
        "business_ids": [business_id],
        "created_at": now_ts,
        **update,
    }
    doc["pos_links"] = {business_id: doc.pop(f"pos_links.{business_id}")}
    await db.users.insert_one(doc)
    return doc["id"]

async def ensure_pos_bridge_business(user: dict, now_ts: str) -> str:
    existing = await db.businesses.find_one({"pos_bridge_default": True}, {"_id": 0})
    if existing:
        return existing["id"]
    doc = {
        "id": str(ObjectId()),
        "name": "Connected POS Business",
        "slug": await unique_business_slug("connected-pos-business"),
        "type": "restaurant",
        "plan": "starter",
        "status": "active",
        "branding": {"primary_color": "#0055FF", "business_name": "Connected POS Business"},
        "owner_id": user["id"],
        "pos_bridge_default": True,
        "pos_synced": True,
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    await db.businesses.insert_one(doc)
    if not is_platform_staff(user):
        await db.users.update_one({"id": user["id"]}, {"$addToSet": {"business_ids": doc["id"]}})
    await seed_business_defaults(doc["id"])
    await ensure_default_outlet_for_business(doc["id"], user=user, sync_to_pos=bool(POS_CORE_API_BASE_URL))
    return doc["id"]

async def local_outlet_id_for_pos_row(row: dict, business_id: str) -> str:
    pos_outlet_id = row.get("outlet_id") or row.get("outletId")
    if not pos_outlet_id or not business_id:
        return str(pos_outlet_id or "")
    existing = await db.outlets.find_one(
        {
            "business_id": business_id,
            "$or": [
                {"id": str(pos_outlet_id)},
                {"pos_external_id": str(pos_outlet_id)},
                {"external_id": str(pos_outlet_id)},
            ],
        },
        {"_id": 0, "id": 1},
    )
    return existing["id"] if existing else str(pos_outlet_id)

async def sync_bridge_pos_record(config: dict, row: dict, business_id: Optional[str], user: dict, now_ts: str) -> str:
    async with import_lease(db.pos_import_leases, business_id, 'privacy-write'):
        if config.get('mode') == 'privacy':
            await assert_pos_row_scope('privacy-erasures', row, business_id)
            return await projection_privacy().apply(row, business_id)
        return await persist_bridge_pos_record(config, row, business_id, user, now_ts)

async def persist_bridge_pos_record(config: dict, row: dict, business_id: Optional[str], user: dict, now_ts: str) -> str:
    await assert_pos_row_scope(config.get("pos_resource") or config["label"], row, business_id)
    external_id = external_id_for(row)
    collection = db[config["collection"]]
    target_business_id = business_id or row.get("business_id") or row.get("businessId") or ""
    row = await projection_privacy().sanitize(row, target_business_id, customer=config.get('collection') in {'pos_customers', 'pos_customer_profiles'})
    if row.get('privacy_erased') and config.get('collection') in {'pos_customers', 'pos_customer_profiles'}:
        external_id = 'erased-' + hmac.new(POS_CORE_API_KEY.encode(), external_id.encode(), hashlib.sha256).hexdigest()
    target_outlet_id = await local_outlet_id_for_pos_row(row, target_business_id)
    existing = await collection.find_one({"business_id": target_business_id, "pos_external_id": external_id}, {"_id": 0})
    status = str(row.get("status") or "active").lower()
    doc = {
        "title": title_for(row, config["label"]),
        "business_id": target_business_id,
        "outlet_id": target_outlet_id,
        "status": status,
        "category": row.get("category") or row.get("channel") or row.get("type") or row.get("source") or config["label"],
        "owner_name": row.get("owner_name") or row.get("customerName") or row.get("customer_name") or row.get("guest_name") or row.get("staffName") or row.get("requestedBy") or row.get("table_number") or "",
        "contact": row.get("contact") or row.get("phone") or row.get("customer_phone") or row.get("trackingToken") or row.get("invoiceNumber") or row.get("reservation_id") or row.get("code") or "",
        "amount": float(next((row[key] for key in ("total", "amount", "guest_count", "party_size", "stock", "count") if row.get(key) is not None and row[key] != ""), 0)),
        "due_date": row.get("due_date") or row.get("reservation_time") or row.get("reserved_at") or row.get("booking_date") or row.get("requiredBy") or "",
        "notes": row.get("notes") or row.get("special_requests") or "",
        "metadata": row,
        "resource": config.get("pos_resource") or config["label"],
        "pos_external_id": external_id,
        "pos_business_id": row.get("business_id") or row.get("businessId") or "",
        "pos_tenant_id": row.get("tenantId") or row.get("tenant_id") or "",
        "pos_synced": True,
        "source_present": True,
        "tenant_scope_status": "verified",
        "created_by": user["id"],
        "created_at": row.get("created_at") or row.get("createdAt") or (existing or {}).get("created_at") or now_ts,
        "updated_at": now_ts,
    }
    if row.get('privacy_erased'):
        doc.update(privacy_erased=True, contact=None, owner_name='Erased customer', notes=None)
        if config.get('collection') in {'pos_customers', 'pos_customer_profiles'}:
            doc.update(title='Erased customer', source_present=False)
    if config.get('collection') == 'pos_bills':
        doc['invoice_number'] = row.get('invoice_number') or row.get('invoiceNumber')
        doc['title'] = doc['invoice_number'] or f'Bill {external_id}'
    if config.get('collection') == 'pos_customers' and 'total_spent' in row:
        doc['amount'] = float(row['total_spent']) if row['total_spent'] is not None else None
        doc['currency'] = row.get('currency')
    if existing:
        await collection.update_one({"id": existing["id"]}, {"$set": doc})
        return existing["id"]
    doc["id"] = str(ObjectId())
    await collection.insert_one(doc)
    return doc["id"]

async def reconcile_complete_pos_snapshot(resource, config, payload, synced, errors, business_id, now_ts):
    supported = set(OPERATIONS) | {"products", "outlets", "orders", "bills", "customers", "staff-shifts", "inventory", "kitchen-tickets", "qr-ordering", "central-kitchen"}
    if resource not in supported or errors or not business_id:
        return
    pagination = payload.get("meta", {}).get("pagination") if isinstance(payload, dict) else None
    if (not isinstance(pagination, dict) or pagination.get("has_more") or pagination.get("strategy") not in {"bounded-id-v1", "frozen-snapshot-v1"}
            or pagination.get("complete_scope") is not True or payload.get("meta", {}).get("complete_snapshot") is not True):
        return  # Never infer removal from an old/truncated export contract.
    scope = await expected_pos_scope_for_business(business_id)
    if resource == 'staff-shifts':
        from pos_membership_reconciliation import reconcile_staff_memberships
        await reconcile_staff_memberships(db.users, business_id, scope, [row['external_id'] for row in synced], now_ts)
    await reconcile_snapshot(db[config["collection"]], business_id=business_id, scope=scope,
        external_ids=[row["external_id"] for row in synced], now=now_ts)
    from pos_reconciliation import reconcile_import_failures
    await reconcile_import_failures(db['pos_change_jobs'], business_id, resource, now_ts, scope)


def is_pos_bridge_key_request(request: Request) -> bool:
    return bool(request.headers.get("x-admincore-api-key") or request.headers.get("x-api-key"))

async def get_pos_bridge_request_user(request: Request) -> dict:
    if is_pos_bridge_key_request(request):
        require_pos_bridge_sync_key(request)
        return await get_pos_bridge_system_user()
    return await get_current_user(request)
@pos_bridge_router.get("/config")
async def get_pos_bridge_config(request: Request):
    if is_pos_bridge_key_request(request):
        require_pos_bridge_sync_key(request)
    else:
        await get_current_user(request)
    return {
        "configured": bool(POS_CORE_API_BASE_URL),
        "base_url": POS_CORE_API_BASE_URL,
        "api_key_configured": bool(POS_CORE_API_KEY),
        "owner_login_configured": bool(POS_CORE_OWNER_EMAIL and POS_CORE_OWNER_PASSWORD),
        "retry_after_seconds": pos_rate_limit_seconds_remaining(),
        "env": {
            "base_url": "POS_CORE_API_BASE_URL",
            "api_key": "POS_CORE_API_KEY",
            "owner_email": "POS_CORE_OWNER_EMAIL",
            "owner_password": "POS_CORE_OWNER_PASSWORD",
        },
    }

@pos_bridge_router.get("/resources")
async def list_pos_bridge_resources(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_pos_bridge_request_user(request)
    await validate_pos_admin_business(user, business_id)
    await require_pos_bridge_access(user, business_id)
    rows = []
    for key, config in POS_BRIDGE_RESOURCES.items():
        if config.get('internal_only'):
            continue
        try:
            authorized = await pos_read_filter(user, business_id, key)
        except HTTPException as exc:
            if exc.status_code == 403:
                continue
            raise
        count_query = {}
        sync_query = {"resource": key, "business_id": business_id or ""}
        if business_id:
            if config["collection"] == "businesses":
                count_query = {"id": business_id}
            elif config["collection"] == "users":
                count_query = {"business_ids": business_id}
            else:
                count_query = {"business_id": business_id}
        elif not is_platform_staff(user):
            sync_query["business_id"] = {"$in": user.get("business_ids", [])}
            if config["collection"] == "businesses":
                count_query = {"id": {"$in": user.get("business_ids", [])}}
            elif config["collection"] == "users":
                count_query = {"business_ids": {"$in": user.get("business_ids", [])}}
            else:
                count_query = {"business_id": {"$in": user.get("business_ids", [])}}
        if config["collection"] not in {"businesses", "users"}:
            count_query = {"$and": [authorized, count_query]}
        last_sync = await db.pos_bridge_sync_runs.find_one(sync_query, {"_id": 0}, sort=[("created_at", -1)])
        rows.append({
            "key": key,
            "label": config["label"],
            "endpoint": config["endpoint"],
            "endpoint_candidates": config.get("endpoint_candidates") or [config["endpoint"]],
            "collection": config["collection"],
            "mode": config["mode"],
            "local_count": await db[config["collection"]].count_documents(count_query),
            "last_sync": last_sync,
        })
    return rows

@pos_bridge_router.get("/proxy/{resource}")
async def proxy_pos_bridge_resource(resource: str, request: Request, business_id: Optional[str] = Query(None)):
    user = await get_pos_bridge_request_user(request)
    if pos_bridge_resource(resource).get('internal_only'):
        require_platform_permission(user, 'privacy.read')
    if not business_id and not is_platform_staff(user):
        raise HTTPException(status_code=400, detail="business_id is required for POS bridge live data")
    await validate_pos_admin_business(user, business_id)
    await require_pos_bridge_access(user, business_id)
    await pos_read_filter(user, business_id, resource)
    params = {}
    payload = await pos_bridge_request(resource, params, business_id=business_id)
    rows = await validate_pos_rows_for_business(resource, await prepare_pos_bridge_rows(resource, payload, business_id), business_id)
    if not is_platform_staff(user):
        business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
        outlets = tenant_read_scope(user, business_id, resource, local_owner=(business or {}).get("owner_id") == user.get("id"))
        if outlets:
            rows = [row for row in rows if (row.get("outlet_id") or row.get("outletId") or (row.get("metadata") or {}).get("outlet_id")) in outlets]
        payload = None
    return {"resource": resource, "rows": rows, "raw": payload}

@pos_bridge_router.post("/sync/{resource}")
async def sync_pos_bridge_resource(resource: str, request: Request, business_id: Optional[str] = Query(None)):
    user = await get_pos_bridge_request_user(request)
    await validate_pos_admin_business(user, business_id)
    await pos_read_filter(user, business_id, resource)
    pos_bridge_resource(resource)
    if not isinstance(business_id, str) or not business_id:
        raise HTTPException(400, "Select a business before syncing")
    async with import_lease(db.pos_import_leases, business_id, resource):
        return await perform_manual_pos_import(resource, request, business_id)


async def perform_manual_pos_import(resource: str, request: Request, business_id: Optional[str]):
    user = await get_pos_bridge_request_user(request)
    if not business_id and not is_platform_staff(user):
        raise HTTPException(status_code=400, detail="business_id is required for POS bridge sync")
    await validate_pos_admin_business(user, business_id)
    await require_pos_bridge_access(user, business_id)
    config = pos_bridge_resource(resource)
    now_ts = datetime.now(timezone.utc).isoformat()
    local_business_id = business_id
    params = {}
    await cleanup_mismatched_pos_imports(resource, local_business_id)
    try:
        payload = await pos_bridge_request(resource, params, business_id=local_business_id)
    except HTTPException as exc:
        detail = compact_bridge_error_detail(exc.detail)
        errors = [{"resource": resource, "reason": bridge_error_message(detail), "detail": detail}]
        sync_run = await record_pos_bridge_sync_run(resource, local_business_id, user, "failed", 0, 1, errors, now_ts)
        await create_audit_log(local_business_id, user["id"], user["email"], "sync_failed", f"pos_bridge:{resource}", None, {"errors": errors})
        raise HTTPException(status_code=exc.status_code, detail={**detail, "sync_run": sync_run}) from exc
    rows = await validate_pos_rows_for_business(resource, await prepare_pos_bridge_rows(resource, payload, local_business_id), local_business_id)
    synced = []
    errors = []
    for row in rows:
        try:
            if config["mode"] == "core" and resource == "businesses":
                synced_id = await sync_bridge_business(row, user, now_ts)
            elif config["mode"] == "core" and resource == "outlets":
                target_business_id = local_business_id or await local_business_id_for_pos_row(row, user, now_ts) or await ensure_pos_bridge_business(user, now_ts)
                if not target_business_id:
                    raise ValueError("business_id is required to sync outlets")
                synced_id = await sync_bridge_outlet(row, target_business_id, now_ts)
            elif config["mode"] == "core" and resource == "products":
                target_business_id = local_business_id or await local_business_id_for_pos_row(row, user, now_ts) or await ensure_pos_bridge_business(user, now_ts)
                if not target_business_id:
                    raise ValueError("business_id is required to sync products")
                synced_id = await sync_bridge_product(row, target_business_id, now_ts)
            elif config["mode"] == "qr_codes":
                target_business_id = local_business_id or await local_business_id_for_pos_row(row, user, now_ts) or await ensure_pos_bridge_business(user, now_ts)
                if not target_business_id:
                    raise ValueError("business_id is required to sync QR codes")
                qr_doc = {
                    "id": str(ObjectId()),
                    "name": title_for(row, "POS QR Code"),
                    "type": "dynamic",
                    "target_url": row.get("target_url") or row.get("url") or f"{POS_CORE_API_BASE_URL}/qr/{row.get('token') or external_id_for(row)}",
                    "business_id": target_business_id,
                    "outlet_id": row.get("outlet_id") or row.get("outletId") or "",
                    "qr_restaurant_id": row.get("tableId") or "",
                    "description": "Synced from POS project",
                    "status": "active" if row.get("active", True) else "inactive",
                    "token": row.get("token") or secrets.token_urlsafe(12),
                    "scan_count": int(row.get("scanCount") or 0),
                    "last_scan_at": row.get("lastScannedAt"),
                    "created_by": user["id"],
                    "source": "pos-bridge",
                    "external_id": external_id_for(row),
                    "created_at": now_ts,
                    "updated_at": now_ts,
                }
                existing = await db.qr_codes.find_one({"source": "pos-bridge", "external_id": qr_doc["external_id"]}, {"_id": 0})
                if existing:
                    synced_id = existing["id"]
                    await db.qr_codes.update_one({"id": synced_id}, {"$set": {k: v for k, v in qr_doc.items() if k not in ["id", "created_at"]}})
                else:
                    synced_id = qr_doc["id"]
                    await db.qr_codes.insert_one(qr_doc)
            else:
                target_business_id = local_business_id or await local_business_id_for_pos_row(row, user, now_ts) or await ensure_pos_bridge_business(user, now_ts)
                synced_id = await sync_bridge_pos_record(config, row, target_business_id, user, now_ts)
                if resource in ["staff", "staff-shifts"]:
                    await sync_bridge_staff_user(row, target_business_id, now_ts)
            synced.append({"external_id": external_id_for(row), "local_id": synced_id})
        except Exception as exc:
            errors.append({"external_id": external_id_for(row), "reason": str(exc)})
    await reconcile_complete_pos_snapshot(resource, config, payload, synced, errors, local_business_id, now_ts)
    status = "success" if not errors else ("partial" if synced else "failed")
    sync_run = await record_pos_bridge_sync_run(resource, local_business_id, user, status, len(synced), len(errors), errors, now_ts)
    await create_audit_log(local_business_id, user["id"], user["email"], "synced" if status != "failed" else "sync_failed", f"pos_bridge:{resource}", None, {"synced": len(synced), "errors": len(errors), "status": status})
    return {"resource": resource, "synced": synced, "errors": errors, "count": len(synced), "error_count": len(errors), "status": status, "sync_run": sync_run}

async def sync_pos_bridge_resource_for_system(resource: str, business_id: Optional[str], user: dict):
    async with import_lease(db.pos_import_leases, business_id, resource):
        return await import_pos_snapshot(resource, business_id, user)


async def import_pos_snapshot(resource: str, business_id: Optional[str], user: dict):
    config = pos_bridge_resource(resource)
    now_ts = datetime.now(timezone.utc).isoformat()
    local_business_id = business_id
    await cleanup_mismatched_pos_imports(resource, local_business_id)
    try:
        payload = await pos_bridge_request(resource, {}, business_id=local_business_id)
    except HTTPException as exc:
        detail = compact_bridge_error_detail(exc.detail)
        errors = [{"resource": resource, "reason": bridge_error_message(detail), "detail": detail}]
        sync_run = await record_pos_bridge_sync_run(resource, local_business_id, user, "failed", 0, 1, errors, now_ts)
        raise HTTPException(
            status_code=exc.status_code,
            detail={**detail, "sync_run": sync_run},
        ) from exc

    rows = await validate_pos_rows_for_business(resource, await prepare_pos_bridge_rows(resource, payload, local_business_id), local_business_id)
    synced = []
    errors = []
    for row in rows:
        try:
            if config["mode"] == "core" and resource == "businesses":
                synced_id = await sync_bridge_business(row, user, now_ts)
            elif config["mode"] == "core" and resource == "outlets":
                target_business_id = local_business_id or await local_business_id_for_pos_row(row, user, now_ts) or await ensure_pos_bridge_business(user, now_ts)
                if not target_business_id:
                    raise ValueError("business_id is required to sync outlets")
                synced_id = await sync_bridge_outlet(row, target_business_id, now_ts)
            elif config["mode"] == "core" and resource == "products":
                target_business_id = local_business_id or await local_business_id_for_pos_row(row, user, now_ts) or await ensure_pos_bridge_business(user, now_ts)
                if not target_business_id:
                    raise ValueError("business_id is required to sync products")
                synced_id = await sync_bridge_product(row, target_business_id, now_ts)
            else:
                target_business_id = local_business_id or await local_business_id_for_pos_row(row, user, now_ts) or await ensure_pos_bridge_business(user, now_ts)
                synced_id = await sync_bridge_pos_record(config, row, target_business_id, user, now_ts)
                if resource in ["staff", "staff-shifts"]:
                    await sync_bridge_staff_user(row, target_business_id, now_ts)
            synced.append({"external_id": external_id_for(row), "local_id": synced_id})
        except Exception as exc:
            errors.append({"external_id": external_id_for(row), "reason": str(exc)})

    await reconcile_complete_pos_snapshot(resource, config, payload, synced, errors, local_business_id, now_ts)
    status = "success" if not errors else ("partial" if synced else "failed")
    sync_run = await record_pos_bridge_sync_run(resource, local_business_id, user, status, len(synced), len(errors), errors, now_ts)
    return {"resource": resource, "synced": synced, "errors": errors, "count": len(synced), "error_count": len(errors), "status": status, "sync_run": sync_run}

@pos_bridge_router.post("/sync-status")
async def receive_pos_bridge_sync_status(request: Request):
    require_pos_bridge_sync_key(request)
    payload = await request.json()
    resource = str(payload.get("resource") or "").strip().lower()
    if resource in ["billing", "bill", "invoice", "invoices"]:
        resource = "bills"
    if resource in ["users", "user", "staffs"]:
        resource = "staff"
    if resource in ["table", "dining-tables", "table-management"]:
        resource = "tables"
    if resource in ["reservation", "table-reservations"]:
        resource = "reservations"
    resource = {"staff": "staff-shifts", "kot": "kitchen-tickets", "qr": "qr-ordering"}.get(resource, resource)
    if resource not in POS_BRIDGE_RESOURCES:
        raise HTTPException(status_code=400, detail=f"Unsupported POS bridge resource: {resource}")

    user = await get_pos_bridge_system_user()
    now_ts = datetime.now(timezone.utc).isoformat()
    business_id = payload.get("admincore_business_id") or payload.get("local_business_id")
    if not business_id:
        # A delayed webhook must not invent a business from a deleted POS test
        # fixture or an unverified tenant. Discovery/import is an explicit step.
        mapped = await db.businesses.find_one({
            "pos_external_id": str(payload.get("business_id") or ""),
            "pos_tenant_id": str(payload.get("tenant_id") or ""),
        }, {"_id": 0, "id": 1})
        business_id = mapped["id"] if mapped else None

    if not business_id:
        raise HTTPException(status_code=409, detail="POS business mapping is required before sync")
    scope = await expected_pos_scope_for_business(business_id)
    validate_pos_row_scope(resource, payload, scope)
    event_id = str(payload.get("id") or "").strip()
    if not event_id or len(event_id) > 200:
        raise HTTPException(status_code=400, detail="A stable POS event ID is required")
    try:
        event = {key: payload.get(key) for key in ["action", "record_id", "changed_at"]}
        if event["action"] == "deleted" and (not isinstance(event["record_id"], str) or not event["record_id"] or len(event["record_id"]) > 200):
            raise HTTPException(status_code=400, detail="Deleted events require a stable POS record ID")
        result = await pos_sync_worker.enqueue(event_id, resource, business_id, event=event)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "accepted": True,
        "message": "POS change notification durably queued",
        "resource": resource,
        "business_id": business_id,
        "result": result,
    }

async def process_pos_change(resource, business_id, event=None):
    if resource == "tenant-entitlements":
        business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
        if not business or not business.get("pos_external_id") or not business.get("pos_tenant_id"):
            raise ValueError("Business must have a verified POS mapping before entitlement sync")
        entitlements = await get_business_entitlements(business_id)
        if entitlements.get("subscription_active") is False:
            # Lifecycle sync enforces suspended/read-only access. Retain the last
            # feature set so expired customers can still read their history.
            return {"status": "success", "error_count": 0, "skipped": "inactive_subscription"}
        payload = pos_entitlement_payload(entitlements)
        headers = await pos_headers_for_admin_business(business_id)
        await pos_core_session_request("PUT", f"admincore/tenants/{business['pos_external_id']}/subscription",
                                       json=payload, extra_headers=headers, use_login=False)
        await db.businesses.update_one({"id": business_id}, {"$set": {
            "pos_entitlements_applied": payload,
            "pos_entitlements_synced_at": datetime.now(timezone.utc).isoformat(),
        }})
        return {"status": "success", "error_count": 0}
    user = await get_pos_bridge_system_user()
    result = await sync_pos_bridge_resource_for_system(resource, business_id, user)
    if result.get("error_count") or result.get("status") != "success":
        return result
    if event and event.get("action") == "snapshot":
        return result
    if event and event.get("action") == "deleted" and resource in set(POS_BRIDGE_RESOURCES) - {"businesses", "staff-shifts"}:
        external_id = event["record_id"]
        # A delayed delete must not remove a record that currently exists in POS.
        if external_id not in {row.get("external_id") for row in result.get("synced", [])}:
            scope = await expected_pos_scope_for_business(business_id)
            collection = db[pos_bridge_resource(resource)["collection"]]
            await collection.update_many({
                "business_id": business_id, "pos_business_id": scope["business_id"],
                "pos_tenant_id": scope["tenant_id"], "pos_external_id": external_id,
            }, {"$set": {"source_present": False, "source_removed_at": datetime.now(timezone.utc).isoformat()}})

    dependencies = {
        "tips": ["tip-payouts"], "payroll": ["payslips"], "customer-profiles": ["loyalty-ledger"],
        "gift-cards": ["gift-card-transactions"], "marketing": ["marketing-delivery"],
        "bills": ["payments", "customers", "reports", "products", "inventory"],
        "orders": ["kitchen-tickets", "customers", "reports"],
        "payments": ["reports"],
        "inventory": ["reports"],
    }
    for dependent in dependencies.get(resource, []):
        dependent_result = await sync_pos_bridge_resource_for_system(dependent, business_id, user)
        if dependent_result.get("error_count") or dependent_result.get("status") != "success":
            return dependent_result
    return result

async def pos_profile_worker_ready():
    return pos_rate_limit_seconds_remaining() == 0


async def pos_import_worker_ready():
    if pos_rate_limit_seconds_remaining() > 0:
        return False
    # Credential writes get the next request window before background imports.
    pending = await db.pos_profile_jobs.find_one(
        {"status": {"$in": ["pending", "running", "retrying"]}}, {"_id": 1})
    return pending is None

async def pos_privacy_worker_ready():
    return pos_rate_limit_seconds_remaining() <= 0


pos_sync_worker = PosSyncWorker(db.pos_change_jobs, process_pos_change, ready=pos_import_worker_ready, timeout_seconds=900)
pos_privacy_worker = PosSyncWorker(db.pos_change_jobs, process_pos_change, ready=pos_privacy_worker_ready, resources=('privacy-erasures',))
async def queued_pos_actor_headers(intent, business_id, resource):
    """Recheck the initiating identity at delivery; POS also checks its live membership.

    A denied header still permits POS to acknowledge an identical committed receipt,
    but never authorizes a new write after an unknown-outcome retry.
    """
    denied = {'x-admincore-actor-mode': 'denied'}
    actor = await db.users.find_one({'id': intent.get('delivery_actor_id') or intent.get('actor_id')}, {'_id': 0})
    if not actor or actor.get('status') != 'active' or actor.get('pos_creation_pending'):
        return denied
    if is_platform_staff(actor):
        if not has_platform_permission(actor, 'users.write' if resource == 'staff' else 'pos.write'):
            return denied
        changes = intent.get('changes') or {}
        if changes.get('role') and not can_assign_role(actor, changes['role']):
            return denied
        if 'business_ids' in changes and actor.get('role') != 'platform_admin':
            return denied
        return {'x-admincore-actor-mode': 'platform'}
    owner_required = resource == 'staff' and intent.get('operation') != 'create'
    try:
        await require_business_management(actor, business_id, owner_only=owner_required,
            target_role=(intent.get('changes') or {}).get('role') if resource == 'staff' else None)
        tenant_read_scope(actor, business_id, 'staff-shifts' if resource == 'staff' else resource)
    except HTTPException:
        return denied
    link = (actor.get('pos_links') or {}).get(business_id) or {}
    if not link.get('user_id'):
        return denied
    return {'x-admincore-actor-mode': 'tenant', 'x-admincore-actor-id': link['user_id'],
            'x-admincore-owner-required': str(owner_required).lower()}


pos_profile_updates = PosProfileUpdates(db, push_admin_user_to_pos, create_audit_log, ready=pos_profile_worker_ready, authorize=queued_pos_actor_headers)
pos_product_commands = ProductCommands(db, push_admin_product_to_pos, delete_admin_product_from_pos, create_audit_log, ready=pos_import_worker_ready, authorize=queued_pos_actor_headers)
pos_outlet_commands = ProductCommands(db, push_admin_outlet_to_pos, delete_admin_outlet_from_pos, create_audit_log,
    ready=pos_import_worker_ready, resource="outlets", fields=("name", "code", "manager_name", "phone", "status"), authorize=queued_pos_actor_headers)


async def reconcile_pos_entitlements():
    """Persist changed grants for the durable worker; never provision unknown tenants."""
    if not POS_CORE_API_BASE_URL:
        return
    async for business in db.businesses.find({"pos_external_id": {"$exists": True}, "pos_tenant_id": {"$exists": True}}, {"_id": 0}):
        if not business.get("pos_external_id") or not business.get("pos_tenant_id"):
            continue
        entitlements = await get_business_entitlements(business["id"])
        if entitlements.get("subscription_active") is False:
            continue
        payload = pos_entitlement_payload(entitlements)
        if payload != business.get("pos_entitlements_applied"):
            previous = await db.pos_change_jobs.find_one({"_id": f"pos-snapshot:{business['id']}:tenant-entitlements"})
            if previous and previous.get("status") == "failed":
                continue  # Preserve the failure and bounded retry count for explicit recovery.
            await pos_sync_worker.enqueue_snapshot("tenant-entitlements", business["id"], priority=100)


async def pos_entitlement_reconciliation_worker():
    while True:
        try:
            await reconcile_pos_entitlements()
        except Exception:
            logger.exception("Could not queue POS entitlement reconciliation")
        await asyncio.sleep(30)


@pos_bridge_router.post("/entitlements/{business_id}/retry", status_code=202)
async def retry_pos_entitlements(business_id: str, request: Request):
    user = await get_current_user(request)
    require_platform_permission(user, "modules.write")
    business = await validate_business_access(user, business_id)
    if not business.get("pos_external_id") or not business.get("pos_tenant_id"):
        raise HTTPException(status_code=409, detail="Complete POS business provisioning before syncing features")
    job = await pos_sync_worker.enqueue_snapshot("tenant-entitlements", business_id, priority=100)
    return {"status": job["status"], "job_id": job["id"]}

@pos_bridge_router.get("/change-jobs")
async def list_pos_change_jobs(request: Request, business_id: Optional[str] = Query(None), snapshots_only: bool = Query(False)):
    user = await get_pos_bridge_request_user(request)
    await require_pos_bridge_access(user, business_id)
    scope = await pos_business_filter(user, business_id)
    if snapshots_only:
        scope["event.action"] = "snapshot"
    return await db.pos_change_jobs.find(scope, {"_id": 0, "lease": 0}).sort("created_at", -1).limit(100).to_list(100)

@pos_bridge_router.post("/queue/{resource}", status_code=202)
async def queue_pos_bridge_resource(resource: str, request: Request, business_id: Optional[str] = Query(None)):
    user = await get_pos_bridge_request_user(request)
    if resource == 'tenant-entitlements':
        require_platform_permission(user, 'modules.write')
    pos_bridge_resource(resource)
    if not business_id:
        raise HTTPException(status_code=400, detail="business_id is required for POS bridge sync")
    await validate_pos_admin_business(user, business_id)
    await pos_read_filter(user, business_id, resource)
    await require_pos_bridge_access(user, business_id)
    await pos_headers_for_admin_business(business_id)
    job = await pos_sync_worker.enqueue_snapshot(resource, business_id, priority=100)
    return {"status": "queued", "business_id": business_id, "resource": resource, "job_id": job["id"]}


@pos_bridge_router.post("/sync-all", status_code=202)
async def sync_all_pos_bridge_resources(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_pos_bridge_request_user(request)
    if not business_id:
        raise HTTPException(status_code=400, detail="business_id is required for POS bridge sync")
    await validate_pos_admin_business(user, business_id)
    await require_pos_bridge_access(user, business_id)
    await pos_headers_for_admin_business(business_id)
    results = {}
    for resource in POS_BRIDGE_RESOURCES:
        if resource == 'tenant-entitlements' and not has_platform_permission(user, 'modules.write'):
            results[resource] = {'resource': resource, 'status': 'not_authorized'}
            continue
        try:
            await pos_read_filter(user, business_id, resource)
        except HTTPException as exc:
            if exc.status_code == 403:
                results[resource] = {"resource": resource, "status": "not_authorized"}
                continue
            raise
        job = await pos_sync_worker.enqueue_snapshot(resource, business_id)
        results[resource] = {"resource": resource, "job_id": job["id"], "status": job["status"]}
    return {"status": "queued", "business_id": business_id, "results": results}


# ===================================================================
# REGISTER ALL ROUTERS
# ===================================================================
api_router.include_router(auth_router)
api_router.include_router(client_router)
api_router.include_router(business_router)
api_router.include_router(outlet_router)
api_router.include_router(product_router)
api_router.include_router(qr_code_router)
api_router.include_router(module_router)
api_router.include_router(user_router)
api_router.include_router(settings_router)
api_router.include_router(ff_router)
api_router.include_router(audit_router)
api_router.include_router(integration_router)
api_router.include_router(plan_router)
api_router.include_router(subscription_router)
api_router.include_router(billing_router)
api_router.include_router(pos_admin_router)
api_router.include_router(control_center_router)
api_router.include_router(pos_bridge_router)
api_router.include_router(dashboard_router)
app.include_router(api_router)


# ===================================================================
# SEED DATA
# ===================================================================
SYSTEM_MODULES = [
    {"slug": "businesses", "name": "Business Management", "description": "Create tenants, manage business profiles, status, ownership, and POS links.", "icon": "Building2", "category": "saas", "is_core": True, "sort_order": 10},
    {"slug": "users_roles", "name": "Users & Roles", "description": "Business users, staff roles, passwords, access scopes, and permission assignment.", "icon": "ShieldCheck", "category": "saas", "is_core": True, "sort_order": 20},
    {"slug": "modules", "name": "Module Control", "description": "Enable, disable, and configure product modules for each tenant.", "icon": "Blocks", "category": "saas", "is_core": True, "sort_order": 30},
    {"slug": "plans", "name": "Plans & Entitlements", "description": "Subscription plans, limits, included modules, trials, and feature gates.", "icon": "Layers3", "category": "saas", "is_core": True, "sort_order": 40},
    {"slug": "subscriptions", "name": "Subscriptions", "description": "Tenant subscription state, renewals, expiry, trials, and billing periods.", "icon": "BadgeDollarSign", "category": "saas", "is_core": True, "sort_order": 50},
    {"slug": "feature_flags", "name": "Feature Flags", "description": "Per-business rollout switches and experimental feature controls.", "icon": "ToggleLeft", "category": "saas", "is_core": False, "sort_order": 60},
    {"slug": "audit_security", "name": "Audit & Security", "description": "Audit logs, security events, data access review, and admin activity history.", "icon": "FileWarning", "category": "saas", "is_core": True, "sort_order": 70},
    {"slug": "notifications", "name": "Notifications", "description": "Email, SMS, push, alert rules, delivery logs, and templates.", "icon": "Bell", "category": "saas", "is_core": False, "sort_order": 80},
    {"slug": "import_export", "name": "Import & Export", "description": "CSV/PDF exports, bulk imports, backups, and data portability.", "icon": "FileUp", "category": "saas", "is_core": False, "sort_order": 90},
    {"slug": "integrations", "name": "Integrations & Webhooks", "description": "External apps, API keys, webhooks, bridge health, and sync configuration.", "icon": "Plug", "category": "saas", "is_core": False, "sort_order": 100},
    {"slug": "pos_bridge", "name": "POS Bridge", "description": "Core POS connection, tenant provisioning, scoped import, and sync health.", "icon": "RefreshCw", "category": "saas", "is_core": True, "sort_order": 105},
    {"slug": "pos", "name": "POS Orders & Sales", "description": "Create orders, track sales, order items, statuses, receipts, and invoices.", "icon": "ShoppingCart", "category": "pos", "is_core": True, "sort_order": 110},
    {"slug": "payments", "name": "Payments", "description": "Cash, card, UPI/manual payments, payment status, refunds, and method reports.", "icon": "CreditCard", "category": "pos", "is_core": True, "sort_order": 120},
    {"slug": "billing", "name": "Billing & Invoicing", "description": "Bills, invoices, subscription charges, payment history, and renewal records.", "icon": "Receipt", "category": "finance", "is_core": True, "sort_order": 130},
    {"slug": "products", "name": "Products & Menu", "description": "Menu/catalog items, pricing, categories, availability, and POS product sync.", "icon": "PackageOpen", "category": "pos", "is_core": True, "sort_order": 140},
    {"slug": "inventory", "name": "Inventory Management", "description": "Stock items, stock in/out, low-stock alerts, movements, and outlet-wise stock.", "icon": "Package", "category": "pos", "is_core": False, "sort_order": 150},
    {"slug": "customers", "name": "Customers CRM", "description": "Customer profiles, contact details, order history, loyalty points, and notes.", "icon": "Users", "category": "engagement", "is_core": False, "sort_order": 160},
    {"slug": "tables", "name": "Table Management", "description": "Dining areas, tables, table status, reservations, and table QR assignment.", "icon": "Grid3X3", "category": "pos", "is_core": False, "sort_order": 170},
    {"slug": "kitchen", "name": "Kitchen / KOT", "description": "Kitchen tickets, item-level prep status, ready/completed flow, and chef performance.", "icon": "ChefHat", "category": "pos", "is_core": False, "sort_order": 180},
    {"slug": "analytics", "name": "Reports & Analytics", "description": "Sales, product, inventory, payment, staff, outlet, tax reports, and exports.", "icon": "BarChart3", "category": "intelligence", "is_core": True, "sort_order": 190},
    {"slug": "taxes_charges", "name": "Taxes & Charges", "description": "Tax rates, service, packaging, delivery charges, and inclusive/exclusive tax.", "icon": "ReceiptText", "category": "finance", "is_core": False, "sort_order": 200},
    {"slug": "discounts_coupons", "name": "Discounts & Coupons", "description": "Percentage/fixed discounts, item/category discounts, coupons, and usage limits.", "icon": "TicketPercent", "category": "engagement", "is_core": False, "sort_order": 210},
    {"slug": "staff", "name": "Staff Shifts & Attendance", "description": "Employee scheduling, shifts, attendance, role screens, and staff performance.", "icon": "UserCog", "category": "hr", "is_core": False, "sort_order": 220},
    {"slug": "payroll", "name": "Payroll", "description": "Salary processing, payouts, payslips, and compensation records.", "icon": "Wallet", "category": "hr", "is_core": False, "sort_order": 230},
    {"slug": "suppliers_purchasing", "name": "Suppliers & Purchasing", "description": "Supplier profiles, purchase orders, receiving, and procurement tracking.", "icon": "Handshake", "category": "operations", "is_core": False, "sort_order": 240},
    {"slug": "expenses", "name": "Expenses", "description": "Business expenses, categories, approval status, receipts, and cash outflow.", "icon": "Banknote", "category": "finance", "is_core": False, "sort_order": 250},
    {"slug": "hardware_printers", "name": "Hardware & Printers", "description": "Printers, KOT printers, cash drawers, terminal setup, and device routing.", "icon": "Printer", "category": "operations", "is_core": False, "sort_order": 260},
    {"slug": "qr_codes", "name": "QR Codes", "description": "Static/dynamic QR codes, table links, regeneration, disable, downloads, and scans.", "icon": "QrCode", "category": "engagement", "is_core": False, "sort_order": 270},
    {"slug": "loyalty", "name": "Loyalty Program", "description": "Points, rewards, customer retention, wallet credit, and redemption rules.", "icon": "Heart", "category": "engagement", "is_core": False, "sort_order": 280},
    {"slug": "delivery", "name": "Delivery Management", "description": "Delivery orders, routes, rider assignment, tracking, and delivery status.", "icon": "Truck", "category": "operations", "is_core": False, "sort_order": 290},
    {"slug": "reservations", "name": "Reservations", "description": "Booking calendar, guest details, table allocation, release, and cancellations.", "icon": "CalendarDays", "category": "operations", "is_core": False, "sort_order": 300},
    {"slug": "franchise", "name": "Franchise / Multi-Outlet", "description": "Multi-outlet controls, central kitchen, outlet operations, allocation, and rollups.", "icon": "Store", "category": "management", "is_core": False, "sort_order": 310},
]

async def sync_system_modules():
    now = datetime.now(timezone.utc).isoformat()
    for mod in SYSTEM_MODULES:
        await db.modules.update_one(
            {"slug": mod["slug"]},
            {
                "$set": {**mod, "updated_at": now},
                "$setOnInsert": {"id": str(ObjectId()), "created_at": now},
            },
            upsert=True,
        )

    businesses = await db.businesses.find({}, {"_id": 0, "id": 1}).to_list(1000)
    for business in businesses:
        for mod in SYSTEM_MODULES:
            await db.business_modules.update_one(
                {"business_id": business["id"], "module_slug": mod["slug"]},
                {
                    "$setOnInsert": {
                        "id": str(ObjectId()),
                        "business_id": business["id"],
                        "module_slug": mod["slug"],
                        "enabled": mod.get("is_core", False),
                        "config": mod.get("default_config", {}),
                        "created_at": now,
                    }
                },
                upsert=True,
            )

    for code, module_slug in FEATURE_TO_MODULE.items():
        await db.features.update_one(
            {"code": code},
            {
                "$set": {
                    "code": code,
                    "module_code": module_slug,
                    "feature_code": code.split(".", 1)[1] if "." in code else code,
                    "name": code.replace("_", " ").replace(".", " / ").title(),
                    "description": f"{code} entitlement",
                    "feature_type": "capability",
                    "active": True,
                    "updated_at": now,
                },
                "$setOnInsert": {"id": str(ObjectId()), "created_at": now},
            },
            upsert=True,
        )

    plan_ids = {}
    for plan_data in SEED_PLANS:
        existing = await db.plans.find_one({"slug": plan_data["slug"]}, {"_id": 0, "id": 1})
        plan_id = existing["id"] if existing else str(ObjectId())
        plan_ids[plan_data["slug"]] = plan_id
        await db.plans.update_one(
            {"slug": plan_data["slug"]},
            {
                "$set": {**plan_data, "id": plan_id, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        await db.plan_entitlements.delete_many({"plan_id": plan_id})
        for feature_code in plan_data.get("entitlement_features", []):
            await db.plan_entitlements.insert_one({"id": str(ObjectId()), "plan_id": plan_id, "feature_code": feature_code, "enabled": True, "created_at": now})
        await db.plan_limits.delete_many({"plan_id": plan_id})
        for limit_code, value in plan_data.get("limit_codes", {}).items():
            await db.plan_limits.insert_one({"id": str(ObjectId()), "plan_id": plan_id, "limit_code": limit_code, "value": value, "created_at": now})

    legacy_enterprise = await db.plans.find_one({"slug": "enterprise", "code": {"$ne": "BUSINESS"}}, {"_id": 0, "id": 1})
    business_plan_id = plan_ids.get("business")
    if legacy_enterprise and business_plan_id:
        await db.subscriptions.update_many({"plan_id": legacy_enterprise["id"]}, {"$set": {"plan_id": business_plan_id, "plan_slug": "business", "updated_at": now}})
        await db.plans.update_one({"id": legacy_enterprise["id"]}, {"$set": {"status": "archived", "is_active": False, "updated_at": now}})

    async for sub in db.subscriptions.find({}, {"_id": 0, "id": 1, "business_id": 1, "plan_id": 1, "plan_slug": 1}):
        plan = await db.plans.find_one({"id": sub.get("plan_id")}, {"_id": 0, "id": 1, "slug": 1})
        if not plan and sub.get("plan_slug"):
            sub_plan_slug = str(sub.get("plan_slug")).lower()
            if sub_plan_slug == "enterprise":
                sub_plan_slug = "business"
            plan = await db.plans.find_one({"$or": [{"slug": sub_plan_slug}, {"code": sub_plan_slug.upper()}]}, {"_id": 0, "id": 1, "slug": 1})
        if not plan and sub.get("business_id"):
            business = await db.businesses.find_one({"id": sub["business_id"]}, {"_id": 0, "plan": 1})
            business_plan_slug = str((business or {}).get("plan") or "free").lower()
            if business_plan_slug == "enterprise":
                business_plan_slug = "business"
            plan = await db.plans.find_one({"$or": [{"slug": business_plan_slug}, {"code": business_plan_slug.upper()}]}, {"_id": 0, "id": 1, "slug": 1})
        if plan and (sub.get("plan_id") != plan["id"] or sub.get("plan_slug") != plan["slug"]):
            await db.subscriptions.update_one(
                {"id": sub["id"]},
                {"$set": {"plan_id": plan["id"], "plan_slug": plan["slug"], "updated_at": now}},
            )

    for addon in ADDON_CATALOG:
        existing = await db.addon_catalog.find_one({"code": addon["code"]}, {"_id": 0, "id": 1})
        addon_id = existing["id"] if existing else str(ObjectId())
        await db.addon_catalog.update_one(
            {"code": addon["code"]},
            {"$set": {**addon, "id": addon_id, "updated_at": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        await db.addon_entitlements.delete_many({"addon_id": addon_id})
        for feature_code in addon.get("features", []):
            await db.addon_entitlements.insert_one({"id": str(ObjectId()), "addon_id": addon_id, "feature_code": feature_code, "enabled": True, "created_at": now})
        await db.addon_limits.delete_many({"addon_id": addon_id})
        for limit_code, value in addon.get("limits", {}).items():
            await db.addon_limits.insert_one({"id": str(ObjectId()), "addon_id": addon_id, "limit_code": limit_code, "value": value, "created_at": now})

FREE_FEATURES = ["pos.basic", "payments.basic", "billing.basic", "products.basic", "reports.basic", "taxes.basic", "discounts.manual", "hardware.receipt_printer", "import_export.basic"]
STARTER_FEATURES = FREE_FEATURES + ["inventory.basic", "crm.basic", "tables.basic", "qr.basic", "kot.basic", "taxes.expanded", "discounts.coupons", "reports.standard", "hardware.kot_printer", "notifications.basic", "expenses.basic"]
PRO_FEATURES = STARTER_FEATURES + ["inventory.advanced", "inventory.batch_tracking", "inventory.recipe_consumption", "crm.advanced", "crm.segmentation", "kot.advanced", "kot.printer_routing", "staff.attendance", "suppliers.basic", "suppliers.advanced", "expenses.advanced", "notifications.advanced", "import_export.advanced", "loyalty.basic", "loyalty.advanced", "reservations.basic", "reservations.advanced", "reports.advanced", "reports.scheduled", "qr.dynamic", "qr.ordering", "qr.analytics", "qr.custom_branding", "discounts.advanced", "hardware.printer_routing", "payroll.basic", "delivery.basic", "payments.refund_reports"]
BUSINESS_FEATURES = PRO_FEATURES + ["franchise.enabled", "central_kitchen.enabled", "payroll.advanced", "delivery.advanced", "integrations.api", "integrations.webhooks", "audit.basic", "audit.advanced", "audit.export", "inventory.multi_outlet", "inventory.stock_transfer", "reports.multi_outlet", "qr.bulk_generation", "loyalty.cross_outlet", "crm.unified_cross_outlet", "kot.central_kitchen", "kot.multi_station"]
PLATFORM_CORE_FEATURES = ["businesses.enabled", "users_roles.enabled", "modules.enabled", "plans.enabled", "subscriptions.enabled", "feature_flags.enabled"]

def modules_for_features(features: list[str]) -> list[str]:
    return sorted({FEATURE_TO_MODULE[feature] for feature in features if feature in FEATURE_TO_MODULE})

def legacy_limits(limit_codes: dict) -> dict:
    return {
        "max_outlets": limit_codes.get("outlets.max", 0),
        "max_users": limit_codes.get("users.max", 0),
        "max_modules": limit_codes.get("modules.max", 0),
        "max_integrations": limit_codes.get("integrations.max", 0),
        "max_products": limit_codes.get("products.max", 0),
        "max_transactions_monthly": limit_codes.get("transactions.monthly", 0),
    }

def plan_seed(code: str, name: str, description: str, features: list[str], limits: dict, sort_order: int, monthly: int = 0, yearly: int = 0, trial_days: int = 0):
    return {
        "code": code,
        "name": name,
        "slug": code.lower() if code != "BUSINESS" else "business",
        "description": description,
        "status": "active",
        "is_active": True,
        "is_default": code == "FREE",
        "trial_days": trial_days,
        "pricing": {"monthly": monthly, "yearly": yearly, "currency": "INR"},
        "limits": legacy_limits(limits),
        "limit_codes": limits,
        "included_modules": modules_for_features(features),
        "entitlement_features": features,
        "features": {feature: True for feature in features},
        "sort_order": sort_order,
    }

SEED_PLANS = [
    plan_seed("FREE", "FREE - Start Billing", "Start billing with core POS, payments, receipts, products, and basic reports.", PLATFORM_CORE_FEATURES + FREE_FEATURES, {"businesses.max": 1, "outlets.max": 1, "users.max": 3, "modules.max": 9, "integrations.max": 0, "products.max": 100, "qr_codes.max": 0, "printers.max": 1, "webhooks.max": 0, "api_requests.monthly": 0, "audit_retention_days": 7, "export_rows.max": 500, "storage_mb": 250, "notifications.monthly": 0, "transactions.monthly": 200}, 0),
    plan_seed("STARTER", "STARTER - Run Your Business", "Run daily restaurant operations with inventory, CRM, tables, QR, KOT, discounts, and alerts.", PLATFORM_CORE_FEATURES + STARTER_FEATURES, {"businesses.max": 1, "outlets.max": 1, "users.max": 8, "modules.max": 18, "integrations.max": 0, "products.max": 500, "qr_codes.max": 100, "printers.max": 2, "webhooks.max": 0, "api_requests.monthly": 0, "audit_retention_days": 30, "export_rows.max": 5000, "storage_mb": 1000, "notifications.monthly": 1000, "transactions.monthly": 1000}, 1, 999, 9990, 14),
    plan_seed("PRO", "PRO - Manage & Grow", "Grow with advanced inventory, KOT, loyalty, reservations, purchasing, staff, and profitability reports.", PLATFORM_CORE_FEATURES + PRO_FEATURES, {"businesses.max": 1, "outlets.max": 3, "users.max": 25, "modules.max": 28, "integrations.max": 2, "products.max": 5000, "qr_codes.max": 1000, "printers.max": 8, "webhooks.max": 2, "api_requests.monthly": 10000, "audit_retention_days": 90, "export_rows.max": 50000, "storage_mb": 5000, "notifications.monthly": 10000, "transactions.monthly": 10000}, 2, 2999, 29990, 14),
    plan_seed("BUSINESS", "BUSINESS / ENTERPRISE - Control at Scale", "Control multi-outlet, franchise, central kitchen, APIs, security, and custom enterprise operations.", PLATFORM_CORE_FEATURES + BUSINESS_FEATURES, {"businesses.max": "unlimited", "outlets.max": "unlimited", "users.max": "unlimited", "modules.max": "unlimited", "integrations.max": "unlimited", "products.max": "unlimited", "qr_codes.max": "unlimited", "printers.max": "unlimited", "webhooks.max": "unlimited", "api_requests.monthly": "unlimited", "audit_retention_days": 365, "export_rows.max": "unlimited", "storage_mb": "unlimited", "notifications.monthly": "unlimited", "transactions.monthly": "unlimited"}, 3, 0, 0, 30),
]

ADDON_CATALOG = [
    {"code": "additional_5_users", "name": "Additional 5 Users", "description": "Adds five user seats.", "status": "active", "pricing": {"monthly": 299, "yearly": 2990, "currency": "INR"}, "features": [], "limits": {"users.max": 5}},
    {"code": "extra_outlet", "name": "Extra Outlet", "description": "Adds one additional outlet.", "status": "active", "pricing": {"monthly": 799, "yearly": 7990, "currency": "INR"}, "features": ["inventory.multi_outlet"], "limits": {"outlets.max": 1}},
    {"code": "advanced_qr_ordering", "name": "Advanced QR Ordering", "description": "Unlocks dynamic QR, ordering, analytics, branding, and more QR capacity.", "status": "active", "pricing": {"monthly": 499, "yearly": 4990, "currency": "INR"}, "features": ["qr.dynamic", "qr.ordering", "qr.analytics", "qr.custom_branding"], "limits": {"qr_codes.max": 500}},
    {"code": "delivery_module", "name": "Delivery Module", "description": "Unlocks delivery operations.", "status": "active", "pricing": {"monthly": 799, "yearly": 7990, "currency": "INR"}, "features": ["delivery.basic", "delivery.advanced"], "limits": {}},
    {"code": "payroll", "name": "Payroll", "description": "Unlocks payroll workflows.", "status": "active", "pricing": {"monthly": 999, "yearly": 9990, "currency": "INR"}, "features": ["payroll.basic", "payroll.advanced"], "limits": {}},
    {"code": "api_access", "name": "API Access", "description": "Unlocks API and webhooks.", "status": "active", "pricing": {"monthly": 1499, "yearly": 14990, "currency": "INR"}, "features": ["integrations.api", "integrations.webhooks"], "limits": {"api_requests.monthly": 50000, "webhooks.max": 10}},
    {"code": "premium_analytics", "name": "Premium Analytics", "description": "Unlocks advanced and scheduled reporting.", "status": "active", "pricing": {"monthly": 999, "yearly": 9990, "currency": "INR"}, "features": ["reports.advanced", "reports.scheduled"], "limits": {"export_rows.max": 50000}},
    {"code": "loyalty", "name": "Loyalty", "description": "Unlocks loyalty rules and rewards.", "status": "active", "pricing": {"monthly": 599, "yearly": 5990, "currency": "INR"}, "features": ["loyalty.basic", "loyalty.advanced"], "limits": {}},
]

DEFAULT_ADMIN_PASSWORD = "admin123"

async def seed_data():
    production = bool(PRODUCTION_CONFIG_STATUS.get("production"))
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@admin.com")
    configured_password = os.environ.get("ADMIN_PASSWORD", "")
    admin_password = configured_password or DEFAULT_ADMIN_PASSWORD

    existing_admin = await db.users.find_one({"email": admin_email})
    if existing_admin:
        # Bootstrap settings create an account once. Restarts must preserve a
        # password the administrator subsequently changed through the application.
        logger.info("Admin user verified, skipping seed")
        return

    now = datetime.now(timezone.utc).isoformat()

    if production or os.environ.get("SEED_DEMO_DATA", "true").lower() == "false":
        # Reference data and an explicit administrator, without invented businesses.
        if len(configured_password) < 12 or configured_password == DEFAULT_ADMIN_PASSWORD:
            logger.error("Refusing to create the bootstrap platform admin: set ADMIN_EMAIL and an ADMIN_PASSWORD of at least 12 characters")
            return
        for mod in SYSTEM_MODULES:
            await db.modules.insert_one({**mod, "id": str(ObjectId()), "created_at": now})
        for plan_data in SEED_PLANS:
            await db.plans.insert_one({**plan_data, "id": str(ObjectId()), "created_at": now, "updated_at": now})
        await db.users.insert_one({"id": str(ObjectId()), "email": admin_email, "password_hash": hash_password(configured_password), "name": "Platform Admin", "role": "platform_admin", "business_ids": [], "status": "active", "created_at": now, "updated_at": now})
        logger.info("Reference catalog and platform administrator initialized")
        return

    logger.info("Seeding database with demo data...")

    # Seed system modules
    for mod in SYSTEM_MODULES:
        mod_doc = {**mod, "id": str(ObjectId()), "created_at": now}
        await db.modules.insert_one(mod_doc)

    # Create businesses
    biz1_id, biz2_id, biz3_id = str(ObjectId()), str(ObjectId()), str(ObjectId())
    businesses = [
        {"id": biz1_id, "name": "Sunrise Restaurant", "slug": "sunrise-restaurant", "type": "restaurant", "plan": "pro", "status": "active", "branding": {"primary_color": "#FF6B35", "business_name": "Sunrise Restaurant"}, "owner_id": "", "created_at": now, "updated_at": now},
        {"id": biz2_id, "name": "Urban Wellness Cafe", "slug": "urban-wellness-cafe", "type": "cafe", "plan": "starter", "status": "active", "branding": {"primary_color": "#2D9B83", "business_name": "Urban Wellness Cafe"}, "owner_id": "", "created_at": now, "updated_at": now},
        {"id": biz3_id, "name": "Metro Retail Hub", "slug": "metro-retail-hub", "type": "retail", "plan": "business", "status": "active", "branding": {"primary_color": "#6C5CE7", "business_name": "Metro Retail Hub"}, "owner_id": "", "created_at": now, "updated_at": now},
    ]
    for biz in businesses:
        await db.businesses.insert_one(biz)

    # Create users
    admin_id = str(ObjectId())
    user1_id, user2_id, user3_id = str(ObjectId()), str(ObjectId()), str(ObjectId())
    users = [
        {"id": admin_id, "email": admin_email, "password_hash": hash_password(admin_password), "name": "Platform Admin", "role": "platform_admin", "business_ids": [], "status": "active", "created_at": now, "updated_at": now},
        {"id": user1_id, "email": "john@sunrise.com", "password_hash": hash_password("password123"), "name": "John Smith", "role": "business_owner", "business_ids": [biz1_id], "status": "active", "created_at": now, "updated_at": now},
        {"id": user2_id, "email": "sarah@urban.com", "password_hash": hash_password("password123"), "name": "Sarah Wilson", "role": "manager", "business_ids": [biz2_id], "status": "active", "created_at": now, "updated_at": now},
        {"id": user3_id, "email": "mike@sunrise.com", "password_hash": hash_password("password123"), "name": "Mike Chen", "role": "staff", "business_ids": [biz1_id], "status": "active", "created_at": now, "updated_at": now},
    ]
    for u in users:
        await db.users.insert_one(u)

    # Create SaaS clients
    clients = [
        {"id": str(ObjectId()), "owner_name": "John Smith", "email": "john@sunrise.com", "phone": "+1-555-1101", "status": "active", "business_ids": [biz1_id], "notes": "Primary client owner for Sunrise Restaurant.", "created_by": admin_id, "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "owner_name": "Sarah Wilson", "email": "sarah@urban.com", "phone": "+1-555-2201", "status": "trial", "business_ids": [biz2_id], "notes": "Trial client evaluating POS and QR modules.", "created_by": admin_id, "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "owner_name": "Metro Retail Operations", "email": "ops@metroretail.example", "phone": "+1-555-3301", "status": "active", "business_ids": [biz3_id], "notes": "Enterprise retail account.", "created_by": admin_id, "created_at": now, "updated_at": now},
    ]
    for c in clients:
        await db.clients.insert_one(c)

    # Update business owners
    await db.businesses.update_one({"id": biz1_id}, {"$set": {"owner_id": user1_id}})
    await db.businesses.update_one({"id": biz2_id}, {"$set": {"owner_id": user2_id}})

    # Create outlets
    outlets = [
        {"id": str(ObjectId()), "business_id": biz1_id, "name": "Downtown Branch", "code": make_outlet_code("OUT"), "address": "123 Main St, Downtown", "phone": "+1-555-0101", "status": "active", "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz1_id, "name": "Airport Terminal", "code": make_outlet_code("OUT"), "address": "Airport Rd, Terminal 2", "phone": "+1-555-0102", "status": "active", "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz2_id, "name": "Greenpark Location", "code": make_outlet_code("OUT"), "address": "45 Park Ave, Greenpark", "phone": "+1-555-0201", "status": "active", "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz3_id, "name": "Central Mall Store", "code": make_outlet_code("OUT"), "address": "500 Central Blvd, Mall", "phone": "+1-555-0301", "status": "active", "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz3_id, "name": "Eastside Location", "code": make_outlet_code("OUT"), "address": "200 East St", "phone": "+1-555-0302", "status": "inactive", "created_at": now, "updated_at": now},
    ]
    for o in outlets:
        await db.outlets.insert_one(o)

    # Create business_modules for each business
    all_modules = await db.modules.find({}, {"_id": 0}).to_list(100)
    for biz_id in [biz1_id, biz2_id, biz3_id]:
        for mod in all_modules:
            await db.business_modules.insert_one({"id": str(ObjectId()), "business_id": biz_id, "module_slug": mod["slug"], "enabled": mod.get("is_core", False), "config": {}, "created_at": now})

    # Create feature flags
    flags = [
        {"id": str(ObjectId()), "business_id": biz1_id, "key": "online_ordering", "name": "Online Ordering", "description": "Allow customers to order online", "enabled": True, "conditions": {}, "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz1_id, "key": "loyalty_points", "name": "Loyalty Points", "description": "Enable loyalty point system", "enabled": True, "conditions": {}, "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz1_id, "key": "table_reservations", "name": "Table Reservations", "description": "Allow online table reservations", "enabled": False, "conditions": {}, "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz2_id, "key": "online_ordering", "name": "Online Ordering", "description": "Allow customers to order online", "enabled": True, "conditions": {}, "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz2_id, "key": "gift_cards", "name": "Gift Cards", "description": "Enable gift card purchases", "enabled": False, "conditions": {}, "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz3_id, "key": "loyalty_points", "name": "Loyalty Points", "description": "Enable loyalty point system", "enabled": True, "conditions": {}, "created_at": now, "updated_at": now},
    ]
    for f in flags:
        await db.feature_flags.insert_one(f)

    # Create settings for each business
    default_settings = [
        {"category": "general", "key": "timezone", "value": "America/New_York", "type": "select", "label": "Timezone", "description": "Business timezone"},
        {"category": "general", "key": "currency", "value": "USD", "type": "select", "label": "Currency", "description": "Default currency"},
        {"category": "general", "key": "language", "value": "en", "type": "select", "label": "Language", "description": "Default language"},
        {"category": "general", "key": "date_format", "value": "MM/DD/YYYY", "type": "select", "label": "Date Format", "description": "Display date format"},
        {"category": "notifications", "key": "email_notifications", "value": "true", "type": "boolean", "label": "Email Notifications", "description": "Send email notifications"},
        {"category": "notifications", "key": "sms_notifications", "value": "false", "type": "boolean", "label": "SMS Notifications", "description": "Send SMS notifications"},
        {"category": "notifications", "key": "push_notifications", "value": "true", "type": "boolean", "label": "Push Notifications", "description": "Browser push notifications"},
        {"category": "branding", "key": "primary_color", "value": "#0055FF", "type": "color", "label": "Primary Color", "description": "Brand primary color"},
        {"category": "branding", "key": "business_tagline", "value": "", "type": "text", "label": "Business Tagline", "description": "Short tagline or slogan"},
    ]
    for biz_id in [biz1_id, biz2_id, biz3_id]:
        for s in default_settings:
            setting_doc = {**s, "id": str(ObjectId()), "business_id": biz_id, "created_at": now}
            await db.settings.insert_one(setting_doc)

    # Create integrations
    integrations = [
        {"id": str(ObjectId()), "business_id": biz1_id, "slug": "stripe", "name": "Stripe Payments", "type": "payment", "status": "active", "config": {}, "webhook_url": "", "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz1_id, "slug": "sendgrid", "name": "SendGrid Email", "type": "email", "status": "inactive", "config": {}, "webhook_url": "", "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz2_id, "slug": "stripe", "name": "Stripe Payments", "type": "payment", "status": "active", "config": {}, "webhook_url": "", "created_at": now, "updated_at": now},
        {"id": str(ObjectId()), "business_id": biz3_id, "slug": "shopify", "name": "Shopify Sync", "type": "ecommerce", "status": "inactive", "config": {}, "webhook_url": "", "created_at": now, "updated_at": now},
    ]
    for intg in integrations:
        await db.integrations.insert_one(intg)

    # Create plans
    plan_ids = {}
    for plan_data in SEED_PLANS:
        plan_doc = {**plan_data, "id": str(ObjectId()), "created_at": now, "updated_at": now}
        await db.plans.insert_one(plan_doc)
        plan_ids[plan_data["slug"]] = plan_doc["id"]

    # Create subscriptions for each business
    biz_plan_map = {biz1_id: "pro", biz2_id: "starter", biz3_id: "business"}
    sub_now = datetime.now(timezone.utc)
    for b_id, p_slug in biz_plan_map.items():
        p_id = plan_ids[p_slug]
        sub_doc = {
            "id": str(ObjectId()), "business_id": b_id, "plan_id": p_id, "plan_slug": p_slug,
            "status": "active", "billing_cycle": "monthly",
            "current_period_start": sub_now.isoformat(),
            "current_period_end": (sub_now + timedelta(days=30)).isoformat(),
            "trial_start": None, "trial_end": None, "cancelled_at": None,
            "billing_provider": None, "billing_provider_id": None, "metadata": {},
            "created_at": now, "updated_at": now,
        }
        await db.subscriptions.insert_one(sub_doc)

    # Create some audit logs
    audit_entries = [
        {"id": str(ObjectId()), "business_id": biz1_id, "user_id": admin_id, "user_email": admin_email, "action": "created", "entity_type": "business", "entity_id": biz1_id, "details": {"name": "Sunrise Restaurant"}, "created_at": now},
        {"id": str(ObjectId()), "business_id": biz2_id, "user_id": admin_id, "user_email": admin_email, "action": "created", "entity_type": "business", "entity_id": biz2_id, "details": {"name": "Urban Wellness Cafe"}, "created_at": now},
        {"id": str(ObjectId()), "business_id": biz1_id, "user_id": user1_id, "user_email": "john@sunrise.com", "action": "toggled", "entity_type": "module", "entity_id": "pos", "details": {"enabled": True}, "created_at": now},
        {"id": str(ObjectId()), "business_id": biz1_id, "user_id": user1_id, "user_email": "john@sunrise.com", "action": "created", "entity_type": "outlet", "entity_id": None, "details": {"name": "Downtown Branch"}, "created_at": now},
    ]
    for entry in audit_entries:
        await db.audit_logs.insert_one(entry)

    # Write test credentials
    creds_path = ROOT_DIR.parent / "memory" / "test_credentials.md"
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text(
        f"# Test Credentials\n\n"
        f"## Admin Account\n- Email: {admin_email}\n- Password: {admin_password}\n- Role: platform_admin\n\n"
        f"## Business Owner\n- Email: john@sunrise.com\n- Password: password123\n- Role: business_owner\n- Business: Sunrise Restaurant\n\n"
        f"## Manager\n- Email: sarah@urban.com\n- Password: password123\n- Role: manager\n- Business: Urban Wellness Cafe\n\n"
        f"## Staff\n- Email: mike@sunrise.com\n- Password: password123\n- Role: staff\n- Business: Sunrise Restaurant\n\n"
        f"## Auth Endpoints\n- POST /api/auth/login\n- POST /api/auth/register\n- GET /api/auth/me\n- POST /api/auth/logout\n- POST /api/auth/refresh\n"
    )
    logger.info("Database seeded successfully with demo data")


# ===================================================================
# STARTUP
# ===================================================================
async def repair_legacy_outlet_indexes():
    try:
        await db.command("ping")
        try:
            await db.outlets.drop_index("code_1")
            logger.info("Dropped legacy unique outlets.code index")
        except PyMongoError:
            pass

        cursor = db.outlets.find({
            "$or": [
                {"code": {"$exists": False}},
                {"code": None},
                {"code": ""},
            ]
        })
        async for outlet in cursor:
            await db.outlets.update_one(
                {"_id": outlet["_id"]},
                {"$set": {"code": make_outlet_code("OUT")}},
            )
    except PyMongoError as exc:
        logger.error("Database is not reachable during startup: %s", exc)
        raise

async def repair_missing_pos_default_outlets():
    cursor = db.businesses.find(
        {
            "$or": [
                {"pos_external_id": {"$exists": True, "$ne": ""}},
                {"pos_synced": True},
                {"pos_bridge_default": True},
            ]
        },
        {"_id": 0, "id": 1, "pos_external_id": 1, "pos_tenant_id": 1},
    )
    async for business in cursor:
        has_outlet = await db.outlets.find_one({"business_id": business["id"]}, {"_id": 0, "id": 1})
        if not has_outlet:
            await ensure_default_outlet_for_business(
                business["id"],
                sync_to_pos=False,
                pos_business_id=business.get("pos_external_id") or business["id"],
                pos_tenant_id=business.get("pos_tenant_id") or f"admincore-{business['id']}",
            )

async def run_startup_maintenance():
    await repair_legacy_outlet_indexes()
    await sync_system_modules()
    await repair_missing_pos_default_outlets()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.user_mfa.create_index("user_id", unique=True)
    await db.login_attempts.create_index("updated_at", expireAfterSeconds=86400)
    await db.clients.create_index("id", unique=True)
    await db.clients.create_index("email")
    await db.clients.create_index("business_ids")
    await db.businesses.create_index("id", unique=True)
    await db.businesses.create_index("slug", unique=True)
    await db.outlets.create_index("id")
    await db.outlets.create_index("business_id")
    await db.outlets.create_index("qr_restaurant_id")
    await db.products.create_index("id", unique=True)
    await db.products.create_index("business_id")
    await db.products.create_index("outlet_id")
    await db.products.create_index([("source", 1), ("external_id", 1)])
    await db.qr_codes.create_index("id", unique=True)
    await db.qr_codes.create_index("business_id")
    await db.qr_codes.create_index("outlet_id")
    await db.qr_codes.create_index("qr_restaurant_id")
    await db.qr_codes.create_index("token")
    await db.modules.create_index("slug", unique=True)
    await db.business_modules.create_index([("business_id", 1), ("module_slug", 1)], unique=True)
    await db.feature_flags.create_index("business_id")
    await db.settings.create_index([("business_id", 1), ("key", 1)])
    await db.audit_logs.create_index([("business_id", 1), ("created_at", -1)])
    await db.integrations.create_index("business_id")
    await db.plans.create_index("slug", unique=True)
    await db.plans.create_index("code")
    await db.plans.create_index("sort_order")
    await db.subscriptions.create_index("business_id", unique=True)
    # Each Razorpay event and payment is stored once, even if two deliveries race.
    await db.billing_events.create_index("event_id", unique=True)
    await db.billing_payments.create_index("payment_id", unique=True)
    await db.billing_payments.create_index([("business_id", 1), ("created_at", -1)])
    await db.subscriptions.create_index("plan_id")
    await db.features.create_index("code", unique=True)
    await db.plan_entitlements.create_index([("plan_id", 1), ("feature_code", 1)])
    await db.plan_limits.create_index([("plan_id", 1), ("limit_code", 1)])
    await db.addon_catalog.create_index("code", unique=True)
    await db.addon_entitlements.create_index([("addon_id", 1), ("feature_code", 1)])
    await db.addon_limits.create_index([("addon_id", 1), ("limit_code", 1)])
    await db.business_addons.create_index([("business_id", 1), ("addon_id", 1)])
    await db.business_entitlement_overrides.create_index([("business_id", 1), ("feature_code", 1)], unique=True)
    await db.business_limit_overrides.create_index([("business_id", 1), ("limit_code", 1)], unique=True)
    await db.subscription_events.create_index([("business_id", 1), ("created_at", -1)])
    await db.billing_events.create_index([("business_id", 1), ("created_at", -1)])
    await db.pos_bridge_sync_runs.create_index([("resource", 1), ("business_id", 1), ("created_at", -1)])
    await db.pos_bridge_sync_runs.create_index("status")
    await db.pos_provisioning_jobs.create_index("id", unique=True)
    await db.pos_profile_jobs.create_index([("status", 1), ("run_after", 1)])
    await db.pos_product_jobs.create_index([("status", 1), ("run_after", 1)])
    await db.pos_outlet_jobs.create_index([("status", 1), ("run_after", 1)])
    await db.pos_reconcile_schedule.create_index([("next_run", 1), ("lease_until", 1)])
    await db.pos_provisioning_jobs.create_index([("status", 1), ("run_after", 1)])
    await db.pos_provisioning_jobs.create_index([("business_id", 1), ("created_at", -1)])
    for config in POS_ADMIN_RESOURCES.values():
        collection = db[config["collection"]]
        await collection.create_index("id", unique=True)
        await collection.create_index("business_id")
        await collection.create_index("outlet_id")
        await collection.create_index("status")
        await collection.create_index("created_at")
        await collection.create_index("pos_external_id")
    for config in POS_BRIDGE_RESOURCES.values():
        collection = db[config["collection"]]
        await collection.create_index("pos_external_id")
        await collection.create_index("pos_synced")
    logger.info("Database indexes created")

@app.on_event("startup")
async def startup():
    if not PRODUCTION_CONFIG_STATUS.get("ok", True):
        raise RuntimeError("Invalid production configuration: " + "; ".join(PRODUCTION_CONFIG_STATUS["errors"]))
    await db.command("ping")
    await db.pos_profile_jobs.create_index([("status", 1), ("run_after", 1)])
    for problem in PRODUCTION_CONFIG_STATUS.get("errors", []):
        logger.error("Insecure production configuration: %s", problem)
    await seed_data()

    async def maintenance_task():
        try:
            await run_startup_maintenance()
        except Exception as exc:
            logger.exception("Startup maintenance failed after API boot: %s", exc)

    asyncio.create_task(maintenance_task())
    if POS_PROVISIONING_WORKER_ENABLED:
        asyncio.create_task(pos_provisioning_worker())
    asyncio.create_task(pos_sync_worker.run_forever())
    asyncio.create_task(pos_privacy_worker.run_forever())
    if POS_CORE_API_BASE_URL:
        interval = int(os.environ.get("POS_RECONCILE_INTERVAL_SECONDS", "600"))
        asyncio.create_task(PosReconciler(db, pos_sync_worker, tuple(POS_BRIDGE_RESOURCES), interval).run_forever())
    asyncio.create_task(pos_entitlement_reconciliation_worker())
    asyncio.create_task(pos_profile_updates.worker.run_forever())
    asyncio.create_task(pos_product_commands.worker.run_forever())
    asyncio.create_task(pos_product_commands.recover_forever())
    asyncio.create_task(pos_outlet_commands.worker.run_forever())
    asyncio.create_task(pos_outlet_commands.recover_forever())
    if subscription_lifecycle.expiry_mode() != "off":
        asyncio.create_task(subscription_expiry_worker())
    logger.info("AdminCore API startup ready")

SUBSCRIPTION_EXPIRY_INTERVAL_SECONDS = max(60, int(os.environ.get("SUBSCRIPTION_EXPIRY_INTERVAL_SECONDS", "3600")))

async def run_subscription_expiry(now=None) -> dict:
    return await subscription_lifecycle.run_expiry(
        db, push_subscription_status_to_pos, record_subscription_event, create_audit_log, logger, now=now)

async def subscription_expiry_worker():
    while True:
        try:
            outcome = await run_subscription_expiry()
            if outcome["changed"] or outcome["failed"]:
                logger.info("Subscription lifecycle run: %s", outcome)
        except Exception as exc:  # keep the loop alive; the next run retries
            logger.exception("Subscription lifecycle run failed: %s", exc)
        await asyncio.sleep(SUBSCRIPTION_EXPIRY_INTERVAL_SECONDS)


# ===================================================================
# REQUEST HYGIENE
# ===================================================================
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))

CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

def csrf_rejected(request: Request) -> bool:
    """Browser sessions are cookies, so a cross-site page could otherwise submit requests with them. Unsafe
    cookie-authenticated requests must send X-Requested-With, which cross-origin pages cannot add without passing
    the CORS allowlist. Bearer-token and key-authenticated integrations are unaffected."""
    if request.method in CSRF_SAFE_METHODS:
        return False
    if not (request.cookies.get("access_token") or request.cookies.get("refresh_token")):
        return False
    if request.headers.get("authorization"):
        return False
    return request.headers.get("x-requested-with", "").lower() != "xmlhttprequest"

@app.middleware("http")
async def request_hygiene(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request_id = re.sub(r"[^A-Za-z0-9._-]", "", request_id)[:64] or uuid.uuid4().hex
    if csrf_rejected(request):
        return Response(status_code=403, content='{"detail":"Missing CSRF header"}', media_type="application/json", headers={"X-Request-ID": request_id})
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        return Response(status_code=413, content='{"detail":"Request body too large"}', media_type="application/json", headers={"X-Request-ID": request_id})
    try:
        response = await call_next(request)
    except Exception:
        # Keep a correlation id on the log line so a customer-reported failure can be found.
        logger.exception("Unhandled error request_id=%s method=%s path=%s", request_id, request.method, request.url.path)
        return Response(status_code=500, content='{"detail":"Internal server error"}', media_type="application/json", headers={"X-Request-ID": request_id})
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    if PRODUCTION_CONFIG_STATUS.get("production"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# ===================================================================
# CORS MIDDLEWARE
# ===================================================================
frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
extra_cors_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "").split(",")
    if origin.strip() and origin.strip() != "*"
]
allowed_origins = list(dict.fromkeys([
    frontend_url,
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://192.168.1.67:3001",
    *extra_cors_origins,
]))
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    # No wildcard default: a credentialed *.vercel.app match would trust every Vercel deployment.
    allow_origin_regex=os.environ.get("CORS_ORIGIN_REGEX") or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count", "X-POS-Refresh-Status", "X-Request-ID", "Retry-After"],
)


# ===================================================================
# SHUTDOWN
# ===================================================================
@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

