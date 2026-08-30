from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import logging
import secrets
import requests
import asyncio
import base64
import re
import time
from io import BytesIO
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Query
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel
from pymongo.errors import PyMongoError
import bcrypt
import jwt as pyjwt
import qrcode

# ===== CONFIGURATION =====
JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
POS_CORE_API_BASE_URL = os.environ.get("POS_CORE_API_BASE_URL", "").rstrip("/")
POS_CORE_API_KEY = os.environ.get("POS_CORE_API_KEY", "")
POS_CORE_OWNER_EMAIL = os.environ.get("POS_CORE_OWNER_EMAIL", "")
POS_CORE_OWNER_PASSWORD = os.environ.get("POS_CORE_OWNER_PASSWORD", "")
ADMINCORE_API_KEY = os.environ.get("ADMINCORE_API_KEY", "")
POS_CORE_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("POS_CORE_REQUEST_TIMEOUT_SECONDS", "20"))
POS_BRIDGE_RESOURCE_TIMEOUT_SECONDS = int(os.environ.get("POS_BRIDGE_RESOURCE_TIMEOUT_SECONDS", "45"))
POS_PROVISIONING_MAX_ATTEMPTS = int(os.environ.get("POS_PROVISIONING_MAX_ATTEMPTS", "6"))
POS_PROVISIONING_WORKER_ENABLED = os.environ.get("POS_PROVISIONING_WORKER_ENABLED", "true").lower() != "false"
POS_PROVISIONING_WORKER_INTERVAL_SECONDS = int(os.environ.get("POS_PROVISIONING_WORKER_INTERVAL_SECONDS", "30"))
POS_PROVISIONING_RETRY_DELAYS_SECONDS = [30, 60, 180, 300, 600, 900]

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
app = FastAPI(title="AdminCore API", version="1.0.0")
api_router = APIRouter(prefix="/api")

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

def create_access_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email, "exp": datetime.now(timezone.utc) + timedelta(hours=1), "type": "access"}
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(days=7), "type": "refresh"}
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def set_auth_cookies(response: Response, access: str, refresh: Optional[str] = None):
    frontend_url = os.environ.get("FRONTEND_URL", "")
    secure_cookie = os.environ.get("COOKIE_SECURE", "").lower() == "true" or frontend_url.startswith("https://")
    same_site = "none" if secure_cookie else "lax"
    response.set_cookie("access_token", access, httponly=True, secure=secure_cookie, samesite=same_site, max_age=3600, path="/")
    if refresh:
        response.set_cookie("refresh_token", refresh, httponly=True, secure=secure_cookie, samesite=same_site, max_age=604800, path="/")

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
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
    if user.get("role") != "platform_admin" and business_id not in user.get("business_ids", []):
        raise HTTPException(status_code=403, detail="Business access denied")
    return business

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
        if key in ["code", "resource", "endpoint", "status_code", "url", "context", "message", "tried"]
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

def pos_core_login(session: requests.Session, headers: dict):
    if not POS_CORE_OWNER_EMAIL or not POS_CORE_OWNER_PASSWORD:
        return
    login_url = f"{POS_CORE_API_BASE_URL}/api/auth/login"
    last_exc = None
    for attempt in range(3):
        try:
            response = session.post(
                login_url,
                json={"email": POS_CORE_OWNER_EMAIL, "password": POS_CORE_OWNER_PASSWORD},
                headers=headers,
                timeout=POS_CORE_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return
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
        "pos_last_provision_attempt_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if status == "synced":
        update.update({"pos_synced": True, "pos_provisioning_error": ""})
    elif error is not None:
        update.update({"pos_synced": False, "pos_provisioning_error": str(error)})
    if extra:
        update.update(extra)
    await db.businesses.update_one({"id": business_id}, {"$set": update})

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
    await db.audit_logs.delete_many({"business_id": business_id})
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
        "owner_password": owner_password or "",
        "actor_id": (actor or {}).get("id", "system"),
        "actor_email": (actor or {}).get("email", "system"),
        "status": "pending",
        "attempts": 0,
        "max_attempts": POS_PROVISIONING_MAX_ATTEMPTS,
        "last_error": "",
        "run_after": run_after or now_ts,
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    await db.pos_provisioning_jobs.update_many(
        {"business_id": business_id, "status": {"$in": ["pending", "retrying"]}},
        {"$set": {"status": "superseded", "updated_at": now_ts}},
    )
    await db.pos_provisioning_jobs.insert_one(job)
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
    await db.pos_provisioning_jobs.update_one(
        {"id": job["id"]},
        {"$set": {"status": "running", "attempts": attempts, "last_attempt_at": now_ts, "updated_at": now_ts}},
    )
    try:
        result = await provision_business_end_to_end(
            business_id,
            owner_name=job.get("owner_name"),
            owner_email=job.get("owner_email"),
            owner_password=job.get("owner_password"),
            actor=actor,
            enqueue_on_failure=False,
        )
        await db.pos_provisioning_jobs.update_one(
            {"id": job["id"]},
            {"$set": {"status": "synced", "result": result, "owner_password": "", "finished_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception as exc:
        detail = pos_error_detail(exc)
        status = "failed" if attempts >= int(job.get("max_attempts") or POS_PROVISIONING_MAX_ATTEMPTS) else "retrying"
        update = {
            "status": status,
            "last_error": detail,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if status == "retrying":
            update["run_after"] = next_pos_provisioning_retry_at(attempts)
        else:
            update["owner_password"] = ""
            update["finished_at"] = datetime.now(timezone.utc).isoformat()
        await db.pos_provisioning_jobs.update_one({"id": job["id"]}, {"$set": update})
        await mark_business_pos_status(business_id, "failed" if status == "failed" else "pending", detail)

async def process_due_pos_provisioning_jobs(limit: int = 3):
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
            "attempts": {"$lt": POS_PROVISIONING_MAX_ATTEMPTS},
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
        return await db.users.find_one({"business_ids": business_id, "role": "business_owner"}, {"_id": 0})
    existing = await db.users.find_one({"email": email}, {"_id": 0})
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
    if len(owner_password) < 6:
        raise HTTPException(status_code=400, detail="Owner password must be at least 6 characters")
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
    return {k: v for k, v in doc.items() if k != "password_hash"}

async def provision_business_end_to_end(
    business_id: str,
    owner_name: Optional[str] = None,
    owner_email: Optional[str] = None,
    owner_password: Optional[str] = None,
    actor: Optional[dict] = None,
    enqueue_on_failure: bool = True,
) -> dict:
    if not POS_CORE_API_BASE_URL:
        await mark_business_pos_status(business_id, "not_configured")
        return {"configured": False, "message": "POS bridge is not configured"}
    await mark_business_pos_status(business_id, "pending")
    try:
        provisioned = await provision_admin_business_to_pos(business_id)
        business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
        owner = await ensure_business_owner_user(
            business_id,
            owner_name,
            owner_email,
            owner_password,
        )
        if not owner:
            raise HTTPException(status_code=400, detail="Business owner email and password are required before POS provisioning can complete")
        await push_admin_user_to_pos(owner, owner_password, allow_generated_password=False)
        outlet = await ensure_default_outlet_for_business(
            business_id,
            user=actor,
            sync_to_pos=True,
            pos_business_id=(business or {}).get("pos_external_id") or provisioned.get("business_id"),
            pos_tenant_id=(business or {}).get("pos_tenant_id") or provisioned.get("tenant_id"),
        )
        await mark_business_pos_status(
            business_id,
            "synced",
            extra={
                "pos_synced_at": datetime.now(timezone.utc).isoformat(),
                "pos_owner_email": owner.get("email", ""),
                "pos_default_outlet_id": (outlet or {}).get("pos_external_id") or (outlet or {}).get("id", ""),
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
async def create_audit_log(business_id, user_id, user_email, action, entity_type, entity_id=None, details=None):
    await db.audit_logs.insert_one({
        "id": str(ObjectId()),
        "business_id": business_id,
        "user_id": user_id,
        "user_email": user_email,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "details": details or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


# ===================================================================
# PYDANTIC MODELS
# ===================================================================
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
    name: Optional[str] = None
    type: Optional[str] = None
    plan: Optional[str] = None
    status: Optional[str] = None
    branding: Optional[dict] = None
    qr_organization_id: Optional[str] = None

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
    name: str
    code: Optional[str] = None
    address: str = ""
    manager_name: str = ""
    phone: str = ""
    status: str = "active"

class OutletUpdate(BaseModel):
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
    price: float = 0
    stock: int = 0
    category: str = "General"
    business_id: Optional[str] = None
    outlet_id: Optional[str] = None
    active: bool = True
    source: str = "admin"
    external_id: Optional[str] = None

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    stock: Optional[int] = None
    category: Optional[str] = None
    business_id: Optional[str] = None
    outlet_id: Optional[str] = None
    active: Optional[bool] = None
    source: Optional[str] = None
    external_id: Optional[str] = None

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
}

CORE_FEATURE_MODULES = {
    "outlets": "businesses",
    "products": "products",
    "users": "users_roles",
    "settings": "modules",
    "feature_flags": "feature_flags",
    "audit_logs": "audit_security",
    "integrations": "integrations",
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
            await db.business_modules.insert_one({
                "id": str(ObjectId()),
                "business_id": business_id,
                "module_slug": mod["slug"],
                "enabled": mod.get("is_core", False),
                "config": {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

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
    await db.business_modules.insert_one(row)
    return row

async def require_business_module_enabled(business_id: Optional[str], module_slug: Optional[str]):
    if not business_id or not module_slug:
        return
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
    if user.get("role") == "platform_admin":
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
    return {
        "business_id": business_id,
        "subscription": sub,
        "plan": plan,
        "subscription_active": subscription_active,
        "features": features,
        "limits": limits,
        "modules_allowed": sorted(modules_allowed),
        "modules_enabled": sorted([key for key, value in module_enabled.items() if value]),
        "effective_modules": effective_modules,
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

async def enforce_limit(business_id: str, limit_code: str, current_usage: int):
    allowed = await get_limit(business_id, limit_code)
    if not limit_allows(allowed, current_usage):
        raise_entitlement_error("PLAN_LIMIT_REACHED", limit=limit_code, allowed=allowed, current=current_usage)


# ===================================================================
# AUTH ROUTES
# ===================================================================
auth_router = APIRouter(prefix="/auth", tags=["auth"])

@auth_router.post("/login")
async def login(req: LoginRequest, response: Response):
    email = req.email.lower().strip()
    try:
        user = await db.users.find_one({"email": email})
    except PyMongoError as exc:
        logger.error("Database unavailable during login: %s", exc)
        raise HTTPException(status_code=503, detail="Database is offline. Start MongoDB and try again.")
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user_id = user["id"]
    access = create_access_token(user_id, email)
    refresh = create_refresh_token(user_id)
    set_auth_cookies(response, access, refresh)
    try:
        await create_audit_log(None, user_id, email, "login", "auth")
    except PyMongoError as exc:
        logger.warning("Login succeeded but audit log write failed: %s", exc)
    return {"id": user_id, "email": user["email"], "name": user["name"], "role": user["role"], "business_ids": user.get("business_ids", []), "status": user.get("status", "active")}

@auth_router.post("/register")
async def register(req: RegisterRequest, response: Response):
    email = req.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    user_id = str(ObjectId())
    doc = {"id": user_id, "email": email, "password_hash": hash_password(req.password), "name": req.name, "role": "business_owner", "business_ids": [], "status": "active", "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.users.insert_one(doc)
    access = create_access_token(user_id, email)
    refresh = create_refresh_token(user_id)
    set_auth_cookies(response, access, refresh)
    return {"id": user_id, "email": email, "name": req.name, "role": "business_owner", "business_ids": [], "status": "active"}

@auth_router.get("/me")
async def get_me(request: Request):
    return await get_current_user(request)

@auth_router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"message": "Logged out"}

@auth_router.post("/refresh")
async def refresh_token(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        access = create_access_token(user["id"], user["email"])
        set_auth_cookies(response, access)
        return {"message": "Token refreshed"}
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


# ===================================================================
# CLIENT ROUTES
# ===================================================================
client_router = APIRouter(prefix="/clients", tags=["clients"])

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
    rows = await db.businesses.find({"id": {"$in": business_ids}}, {"_id": 0, "id": 1}).to_list(200)
    found = {row["id"] for row in rows}
    missing = [business_id for business_id in business_ids if business_id not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown business ids: {', '.join(missing)}")
    if user["role"] != "platform_admin":
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
    elif user["role"] != "platform_admin":
        query["business_ids"] = {"$in": user.get("business_ids", [])}
    if status and status != "all":
        query["status"] = status
    if search:
        query["$or"] = [
            {"owner_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
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
    await validate_client_business_ids(user, existing.get("business_ids", []))
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
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
    await validate_client_business_ids(user, existing.get("business_ids", []))
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
    if user["role"] == "platform_admin":
        businesses = await db.businesses.find({}, {"_id": 0}).to_list(100)
    else:
        businesses = await db.businesses.find({"id": {"$in": user.get("business_ids", [])}}, {"_id": 0}).to_list(100)
    return businesses

@business_router.post("")
async def create_business(data: BusinessCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    owner_email = (data.owner_email or "").strip().lower()
    owner_name = (data.owner_name or "").strip()
    owner_password = data.owner_password or ""
    if POS_CORE_API_BASE_URL:
        if not owner_email:
            raise HTTPException(status_code=400, detail="Owner email is required so the business can login to POS")
        if not owner_password or len(owner_password) < 6:
            raise HTTPException(status_code=400, detail="Owner password must be at least 6 characters")
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
    await db.users.update_one({"id": user["id"]}, {"$addToSet": {"business_ids": biz_id}})
    await seed_business_defaults(biz_id)
    await create_audit_log(biz_id, user["id"], user["email"], "created", "business", biz_id, {"name": data.name})
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
        asyncio.create_task(process_due_pos_provisioning_jobs(limit=1))
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
        **created_doc,
        "created_owner": owner,
        "created_outlet": created_outlet,
        "created_qr_code": created_qr_code,
        "pos_provisioned": False,
        "pos_provisioning_job": pos_job,
    }

@business_router.post("/{business_id}/provision-pos")
async def provision_business_to_pos_endpoint(business_id: str, data: BusinessProvisionRequest, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await validate_business_access(user, business_id)
    owner_email = (data.owner_email or "").strip().lower()
    owner_password = data.owner_password or ""
    if POS_CORE_API_BASE_URL:
        if not owner_email:
            raise HTTPException(status_code=400, detail="Owner email is required so the business can login to POS")
        if not owner_password or len(owner_password) < 6:
            raise HTTPException(status_code=400, detail="Owner password must be at least 6 characters")
    await ensure_business_owner_user(
        business_id,
        data.owner_name,
        owner_email,
        owner_password,
    )
    job = await queue_pos_provisioning_job(
        business_id,
        data.owner_name,
        owner_email,
        owner_password,
        user,
    )
    asyncio.create_task(process_due_pos_provisioning_jobs(limit=1))
    await create_audit_log(business_id, user["id"], user["email"], "provision_queued", "business", business_id, {"target": "pos", "job_id": job["id"]})
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
    return {"message": "POS provisioning queued", "job": job, "business": business}

@business_router.get("/{business_id}")
async def get_business(business_id: str, request: Request):
    user = await get_current_user(request)
    return await validate_business_access(user, business_id)

@business_router.put("/{business_id}")
async def update_business(business_id: str, data: BusinessUpdate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await validate_business_access(user, business_id)
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.businesses.update_one({"id": business_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Business not found")
    await create_audit_log(business_id, user["id"], user["email"], "updated", "business", business_id, update_data)
    return await db.businesses.find_one({"id": business_id}, {"_id": 0})

@business_router.delete("/{business_id}")
async def delete_business(business_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Only platform admins can delete businesses")
    result = await db.businesses.delete_one({"id": business_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Business not found")
    await db.outlets.delete_many({"business_id": business_id})
    await db.business_modules.delete_many({"business_id": business_id})
    await db.feature_flags.delete_many({"business_id": business_id})
    await db.settings.delete_many({"business_id": business_id})
    await db.integrations.delete_many({"business_id": business_id})
    await create_audit_log(business_id, user["id"], user["email"], "deleted", "business", business_id)
    return {"message": "Business deleted"}

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

@outlet_router.get("/business/{business_id}")
async def list_outlets(business_id: str, request: Request):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["outlets"])
    if await is_pos_connected_business(business_id):
        await cleanup_mismatched_pos_imports("outlets", business_id)
    return await db.outlets.find({"business_id": business_id, "tenant_scope_status": {"$ne": "quarantined"}}, {"_id": 0}).to_list(100)

@outlet_router.post("/business/{business_id}")
async def create_outlet(business_id: str, data: OutletCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await validate_business_access(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["outlets"])
    await enforce_limit(business_id, "outlets.max", await db.outlets.count_documents({"business_id": business_id, "status": "active"}))
    status = (data.status or "active").lower()
    if status not in ["active", "inactive"]:
        raise HTTPException(status_code=400, detail="status must be active or inactive")
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0, "pos_external_id": 1, "pos_tenant_id": 1})
    doc = {
        "id": str(ObjectId()),
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
    await db.outlets.insert_one(doc)
    try:
        await push_admin_outlet_to_pos(doc)
    except HTTPException as exc:
        if POS_CORE_API_BASE_URL:
            await db.outlets.delete_one({"id": doc["id"]})
            raise HTTPException(status_code=502, detail=f"Outlet was not created because POS sync failed: {exc.detail}") from exc
    await create_audit_log(business_id, user["id"], user["email"], "created", "outlet", doc["id"], {"name": data.name})
    outlet = await db.outlets.find_one({"id": doc["id"]}, {"_id": 0})
    return outlet

@outlet_router.put("/{outlet_id}")
async def update_outlet(outlet_id: str, data: OutletUpdate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    outlet = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
    if not outlet:
        raise HTTPException(status_code=404, detail="Outlet not found")
    await validate_business_access(user, outlet.get("business_id"))
    await require_business_module_enabled(outlet.get("business_id"), CORE_FEATURE_MODULES["outlets"])
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if "code" in update_data:
        update_data["code"] = (update_data["code"] or "").strip() or outlet.get("code") or make_outlet_code("OUT")
    if "status" in update_data:
        update_data["status"] = (update_data["status"] or "active").lower()
        if update_data["status"] not in ["active", "inactive"]:
            raise HTTPException(status_code=400, detail="status must be active or inactive")
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.outlets.update_one({"id": outlet_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Outlet not found")
    outlet = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
    try:
        await push_admin_outlet_to_pos(outlet)
    except HTTPException as exc:
        if POS_CORE_API_BASE_URL:
            raise HTTPException(status_code=502, detail=f"Outlet was not updated because POS sync failed: {exc.detail}") from exc
    outlet = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
    await create_audit_log(outlet["business_id"], user["id"], user["email"], "updated", "outlet", outlet_id, update_data)
    return outlet

@outlet_router.delete("/{outlet_id}")
async def delete_outlet(outlet_id: str, request: Request):
    user = await get_current_user(request)
    outlet = await db.outlets.find_one({"id": outlet_id})
    if not outlet:
        raise HTTPException(status_code=404, detail="Outlet not found")
    await validate_business_access(user, outlet.get("business_id"))
    await require_business_module_enabled(outlet.get("business_id"), CORE_FEATURE_MODULES["outlets"])
    try:
        await delete_admin_outlet_from_pos(outlet)
    except HTTPException as exc:
        if POS_CORE_API_BASE_URL:
            raise HTTPException(status_code=502, detail=f"Outlet was not deleted because POS sync failed: {exc.detail}") from exc
    await db.outlets.delete_one({"id": outlet_id})
    await create_audit_log(outlet["business_id"], user["id"], user["email"], "deleted", "outlet", outlet_id)
    return {"message": "Outlet deleted"}


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
):
    user = await get_current_user(request)
    if business_id:
        await validate_business_access(user, business_id)
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["products"])
    query = {}
    if business_id:
        query["business_id"] = business_id
    elif user["role"] != "platform_admin":
        query["business_id"] = {"$in": user.get("business_ids", [])}
    if outlet_id:
        query["outlet_id"] = outlet_id
    if q:
        query["name"] = {"$regex": q, "$options": "i"}
    query["tenant_scope_status"] = {"$ne": "quarantined"}
    if business_id and await is_pos_connected_business(business_id):
        await cleanup_mismatched_pos_imports("products", business_id)
    products = await db.products.find(query, {"_id": 0}).sort("name", 1).to_list(500)
    if business_id and not products and not q and await is_pos_connected_business(business_id):
        now_ts = datetime.now(timezone.utc).isoformat()
        try:
            payload = await pos_bridge_request("products", {}, business_id=business_id)
            rows = await prepare_pos_bridge_rows("products", payload, business_id)
            rows = await validate_pos_rows_for_business("products", rows, business_id)
            for row in rows:
                await sync_bridge_product(row, business_id, now_ts)
            products = await db.products.find(query, {"_id": 0}).sort("name", 1).to_list(500)
        except HTTPException as exc:
            logger.warning("Could not auto-sync POS products for business %s: %s", business_id, exc.detail)
            raise HTTPException(status_code=exc.status_code, detail={
                "code": "POS_PRODUCTS_SYNC_FAILED",
                "message": exc.detail,
                "detail": "This business is linked to POS, but AdminCore could not load POS products.",
            }) from exc
    return products

@product_router.post("")
async def create_product(data: ProductCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if not data.business_id:
        raise HTTPException(status_code=400, detail="business_id is required")
    await validate_business_access(user, data.business_id)
    await require_business_module_enabled(data.business_id, CORE_FEATURE_MODULES["products"])
    await enforce_limit(data.business_id, "products.max", await db.products.count_documents({"business_id": data.business_id}))
    now_ts = datetime.now(timezone.utc).isoformat()
    doc = {
        **data.model_dump(),
        "id": str(ObjectId()),
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    if doc.get("external_id"):
        existing = await db.products.find_one({"source": doc.get("source"), "external_id": doc["external_id"]})
        if existing:
            raise HTTPException(status_code=400, detail="Product external_id already exists for this source")
    await db.products.insert_one(doc)
    pos_push = None
    try:
        pos_push = await push_admin_product_to_pos({k: v for k, v in doc.items() if k != "_id"})
    except HTTPException as exc:
        if POS_CORE_API_BASE_URL and doc.get("business_id"):
            await db.products.delete_one({"id": doc["id"]})
            raise HTTPException(status_code=502, detail=f"Product was not created because POS sync failed: {exc.detail}")
    await create_audit_log(doc.get("business_id"), user["id"], user["email"], "created", "product", doc["id"], {"name": doc["name"], "pos_pushed": bool(pos_push)})
    return await db.products.find_one({"id": doc["id"]}, {"_id": 0})

@product_router.get("/{product_id}")
async def get_product(product_id: str, request: Request):
    user = await get_current_user(request)
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await validate_business_access(user, product.get("business_id"))
    await require_business_module_enabled(product.get("business_id"), CORE_FEATURE_MODULES["products"])
    return product

@product_router.put("/{product_id}")
async def update_product(product_id: str, data: ProductUpdate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await validate_business_access(user, product.get("business_id"))
    await require_business_module_enabled(product.get("business_id"), CORE_FEATURE_MODULES["products"])
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if "business_id" in update_data:
        await validate_business_access(user, update_data.get("business_id"))
        await require_business_module_enabled(update_data.get("business_id"), CORE_FEATURE_MODULES["products"])
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.products.update_one({"id": product_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    pos_push = None
    try:
        pos_push = await push_admin_product_to_pos(product)
    except HTTPException as exc:
        if POS_CORE_API_BASE_URL and product.get("business_id"):
            raise HTTPException(status_code=502, detail=f"Product was updated in AdminCore but POS sync failed: {exc.detail}")
    await create_audit_log(product.get("business_id"), user["id"], user["email"], "updated", "product", product_id, {**update_data, "pos_pushed": bool(pos_push)})
    return product

@product_router.delete("/{product_id}")
async def delete_product(product_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await validate_business_access(user, product.get("business_id"))
    await require_business_module_enabled(product.get("business_id"), CORE_FEATURE_MODULES["products"])
    try:
        await delete_admin_product_from_pos(product)
    except HTTPException as exc:
        if POS_CORE_API_BASE_URL and product.get("business_id"):
            raise HTTPException(status_code=502, detail=f"Product was not deleted because POS sync failed: {exc.detail}")
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
    elif user["role"] != "platform_admin":
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
            bm = {
                "id": str(ObjectId()),
                "business_id": business_id,
                "module_slug": mod["slug"],
                "enabled": mod.get("is_core", False),
                "config": mod.get("default_config", {}),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.business_modules.insert_one(bm)
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
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await validate_business_access(user, business_id)
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
    update = {"enabled": data.enabled}
    if data.config:
        update["config"] = data.config
    await db.business_modules.update_one({"business_id": business_id, "module_slug": module_slug}, {"$set": update}, upsert=True)
    await create_audit_log(business_id, user["id"], user["email"], "toggled", "module", module_slug, {"enabled": data.enabled})
    return {"message": f"Module {module_slug} {'enabled' if data.enabled else 'disabled'}"}


# ===================================================================
# USER ROUTES
# ===================================================================
user_router = APIRouter(prefix="/users", tags=["users"])
USER_ROLES = ["platform_admin", "business_owner", "manager", "staff", "support_admin"]
USER_STATUSES = ["active", "inactive"]

@user_router.get("")
async def list_users(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    if business_id:
        await validate_business_access(user, business_id)
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["users"])
    if user["role"] == "platform_admin":
        query = {"business_ids": business_id, "role": {"$ne": "platform_admin"}} if business_id else {}
    else:
        bid = business_id or (user.get("business_ids", [""])[0] if user.get("business_ids") else "")
        query = {"business_ids": bid} if bid else {}
    users = await db.users.find(query, {"_id": 0, "password_hash": 0}).to_list(200)
    return users

@user_router.post("")
async def create_user(data: UserCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    email = data.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already exists")
    if data.role not in USER_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of: {', '.join(USER_ROLES)}")
    if user["role"] != "platform_admin" and data.role in ["platform_admin", "support_admin"]:
        raise HTTPException(status_code=403, detail="Only platform admins can create platform roles")
    business_ids = list(dict.fromkeys(data.business_ids or []))
    await validate_client_business_ids(user, business_ids)
    for business_id in business_ids:
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
        await enforce_limit(business_id, "users.max", await db.users.count_documents({"business_ids": business_id}))
    user_id = str(ObjectId())
    doc = {"id": user_id, "email": email, "password_hash": hash_password(data.password), "name": data.name, "role": data.role, "business_ids": business_ids, "status": "active", "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.users.insert_one(doc)
    pos_push = None
    try:
        pos_push = await push_admin_user_to_pos({k: v for k, v in doc.items() if k != "password_hash"}, data.password)
    except HTTPException as exc:
        logger.warning("Could not push AdminCore user to POS: %s", exc.detail)
        if POS_CORE_API_BASE_URL and business_ids:
            await db.users.delete_one({"id": user_id})
            raise HTTPException(status_code=502, detail=f"User was not created because POS sync failed: {exc.detail}")
    biz_id = data.business_ids[0] if data.business_ids else None
    await create_audit_log(biz_id, user["id"], user["email"], "created", "user", user_id, {"email": email, "role": data.role, "pos_pushed": bool(pos_push)})
    created = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    return created

@user_router.put("/{user_id}")
async def update_user(user_id: str, data: UserUpdate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    existing = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
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
            if len(password) < 6:
                raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
            update_data["password_hash"] = hash_password(password)
    if "role" in update_data:
        if update_data["role"] not in USER_ROLES:
            raise HTTPException(status_code=400, detail=f"role must be one of: {', '.join(USER_ROLES)}")
        if user["role"] != "platform_admin" and update_data["role"] in ["platform_admin", "support_admin"]:
            raise HTTPException(status_code=403, detail="Only platform admins can assign platform roles")
    if "status" in update_data:
        update_data["status"] = (update_data["status"] or "active").lower()
        if update_data["status"] not in USER_STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(USER_STATUSES)}")
    if "business_ids" in update_data:
        update_data["business_ids"] = list(dict.fromkeys(update_data["business_ids"] or []))
        await validate_client_business_ids(user, update_data["business_ids"])
        for business_id in update_data["business_ids"]:
            await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
    await validate_business_access_many(user, existing.get("business_ids", []))
    for business_id in existing.get("business_ids", []):
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.users.update_one({"id": user_id}, {"$set": update_data})
    updated_user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    pos_push = None
    try:
        pos_push = await push_admin_user_to_pos(updated_user, data.password)
    except HTTPException as exc:
        logger.warning("Could not push AdminCore user update to POS: %s", exc.detail)
        if POS_CORE_API_BASE_URL and updated_user.get("business_ids"):
            raise HTTPException(status_code=502, detail=f"User was updated in AdminCore but POS sync failed: {exc.detail}")
    await create_audit_log((update_data.get("business_ids") or existing.get("business_ids") or [None])[0], user["id"], user["email"], "updated", "user", user_id, {**{k: v for k, v in update_data.items() if k != "password_hash"}, "pos_pushed": bool(pos_push)})
    return await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})

@user_router.post("/{user_id}/sync-pos")
async def sync_user_to_pos(user_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    target_user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["role"] != "platform_admin":
        allowed_business_ids = set(user.get("business_ids", []))
        if not allowed_business_ids.intersection(set(target_user.get("business_ids", []))):
            raise HTTPException(status_code=403, detail="Access denied")
    for business_id in target_user.get("business_ids", []):
        await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
    result = await push_admin_user_to_pos(target_user)
    if not result:
        raise HTTPException(status_code=400, detail="User is not assigned to a POS-syncable business")
    await create_audit_log((target_user.get("business_ids") or [None])[0], user["id"], user["email"], "synced", "user", user_id, {"target": "pos"})
    return {"message": "User synced to POS", "result": result}

@user_router.delete("/{user_id}")
async def delete_user(user_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    target = await db.users.find_one({"id": user_id}, {"_id": 0, "business_ids": 1})
    if target:
        await validate_business_access_many(user, target.get("business_ids", []))
        for business_id in target.get("business_ids", []):
            await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["users"])
    result = await db.users.delete_one({"id": user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    await create_audit_log(None, user["id"], user["email"], "deleted", "user", user_id)
    return {"message": "User deleted"}


# ===================================================================
# SETTINGS ROUTES
# ===================================================================
settings_router = APIRouter(prefix="/settings", tags=["settings"])

@settings_router.get("/business/{business_id}")
async def list_settings(business_id: str, request: Request, category: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["settings"])
    query = {"business_id": business_id}
    if category:
        query["category"] = category
    return await db.settings.find(query, {"_id": 0}).to_list(200)

@settings_router.put("/business/{business_id}/{key}")
async def update_setting(business_id: str, key: str, data: SettingUpdate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner", "manager"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await validate_business_access(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["settings"])
    result = await db.settings.update_one({"business_id": business_id, "key": key}, {"$set": {"value": data.value, "updated_at": datetime.now(timezone.utc).isoformat()}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Setting not found")
    await create_audit_log(business_id, user["id"], user["email"], "updated", "setting", key, {"value": data.value})
    return {"message": "Setting updated"}


# ===================================================================
# FEATURE FLAGS ROUTES
# ===================================================================
ff_router = APIRouter(prefix="/feature-flags", tags=["feature-flags"])

@ff_router.get("/business/{business_id}")
async def list_feature_flags(business_id: str, request: Request):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["feature_flags"])
    return await db.feature_flags.find({"business_id": business_id}, {"_id": 0}).to_list(100)

@ff_router.post("/business/{business_id}")
async def create_feature_flag(business_id: str, data: FeatureFlagCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await validate_business_access(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["feature_flags"])
    doc = {"id": str(ObjectId()), "business_id": business_id, "key": data.key, "name": data.name, "description": data.description, "enabled": data.enabled, "conditions": {}, "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.feature_flags.insert_one(doc)
    await create_audit_log(business_id, user["id"], user["email"], "created", "feature_flag", doc["id"], {"key": data.key})
    return {k: v for k, v in doc.items() if k != "_id"}

@ff_router.put("/{flag_id}")
async def update_feature_flag(flag_id: str, data: FeatureFlagUpdate, request: Request):
    user = await get_current_user(request)
    existing = await db.feature_flags.find_one({"id": flag_id}, {"_id": 0, "business_id": 1})
    if not existing:
        raise HTTPException(status_code=404, detail="Feature flag not found")
    await validate_business_access(user, existing.get("business_id"))
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
    flag = await db.feature_flags.find_one({"id": flag_id})
    if not flag:
        raise HTTPException(status_code=404, detail="Feature flag not found")
    await validate_business_access(user, flag.get("business_id"))
    await require_business_module_enabled(flag.get("business_id"), CORE_FEATURE_MODULES["feature_flags"])
    await db.feature_flags.delete_one({"id": flag_id})
    await create_audit_log(flag["business_id"], user["id"], user["email"], "deleted", "feature_flag", flag_id)
    return {"message": "Feature flag deleted"}


# ===================================================================
# AUDIT LOG ROUTES
# ===================================================================
audit_router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])

@audit_router.get("/business/{business_id}")
async def list_audit_logs(business_id: str, request: Request, limit: int = Query(50), skip: int = Query(0)):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["audit_logs"])
    logs = await db.audit_logs.find({"business_id": business_id}, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.audit_logs.count_documents({"business_id": business_id})
    return {"logs": logs, "total": total}

@audit_router.get("")
async def list_all_audit_logs(request: Request, limit: int = Query(50), skip: int = Query(0)):
    user = await get_current_user(request)
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
    logs = await db.audit_logs.find({}, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.audit_logs.count_documents({})
    return {"logs": logs, "total": total}


# ===================================================================
# INTEGRATION ROUTES
# ===================================================================
integration_router = APIRouter(prefix="/integrations", tags=["integrations"])

@integration_router.get("/business/{business_id}")
async def list_integrations(business_id: str, request: Request):
    user = await get_current_user(request)
    await validate_business_access(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["integrations"])
    return await db.integrations.find({"business_id": business_id}, {"_id": 0}).to_list(100)

@integration_router.post("/business/{business_id}")
async def create_integration(business_id: str, data: IntegrationCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await validate_business_access(user, business_id)
    await require_business_module_enabled(business_id, CORE_FEATURE_MODULES["integrations"])
    doc = {"id": str(ObjectId()), "business_id": business_id, "slug": data.slug, "name": data.name, "type": data.type, "status": "inactive", "config": data.config, "webhook_url": "", "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.integrations.insert_one(doc)
    await create_audit_log(business_id, user["id"], user["email"], "created", "integration", doc["id"], {"name": data.name})
    return {k: v for k, v in doc.items() if k != "_id"}

@integration_router.put("/{integration_id}")
async def update_integration(integration_id: str, data: IntegrationUpdate, request: Request):
    user = await get_current_user(request)
    existing = await db.integrations.find_one({"id": integration_id}, {"_id": 0, "business_id": 1})
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
    await validate_business_access(user, existing.get("business_id"))
    await require_business_module_enabled(existing.get("business_id"), CORE_FEATURE_MODULES["integrations"])
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.integrations.update_one({"id": integration_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Integration not found")
    return await db.integrations.find_one({"id": integration_id}, {"_id": 0})

@integration_router.delete("/{integration_id}")
async def delete_integration(integration_id: str, request: Request):
    user = await get_current_user(request)
    intg = await db.integrations.find_one({"id": integration_id})
    if not intg:
        raise HTTPException(status_code=404, detail="Integration not found")
    await validate_business_access(user, intg.get("business_id"))
    await require_business_module_enabled(intg.get("business_id"), CORE_FEATURE_MODULES["integrations"])
    await db.integrations.delete_one({"id": integration_id})
    await create_audit_log(intg["business_id"], user["id"], user["email"], "deleted", "integration", integration_id)
    return {"message": "Integration deleted"}


# ===================================================================
# DASHBOARD ROUTES
# ===================================================================
dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])

@dashboard_router.get("/stats")
async def get_dashboard_stats(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    if business_id:
        await validate_business_access(user, business_id)
        if await is_pos_connected_business(business_id):
            for bridge_resource in ["outlets", "products", "orders", "bills", "payments", "tables", "reservations", "customers", "kitchen-tickets", "inventory", "staff-shifts", "reports"]:
                await cleanup_mismatched_pos_imports(bridge_resource, business_id)
        safe_business_query = {"business_id": business_id, "tenant_scope_status": {"$ne": "quarantined"}}
        total_outlets = await db.outlets.count_documents({**safe_business_query, "status": "active"})
        total_users = await db.users.count_documents({"business_ids": business_id})
        total_modules = await db.business_modules.count_documents({"business_id": business_id, "enabled": True})
        total_flags = await db.feature_flags.count_documents({"business_id": business_id})
        total_products = await db.products.count_documents(safe_business_query)
        total_orders = await db.pos_sales_orders.count_documents(safe_business_query)
        total_bills = await db.pos_bills.count_documents(safe_business_query)
        total_inventory_items = await db.pos_inventory_admin.count_documents(safe_business_query)
        total_staff_records = await db.pos_staff_shifts.count_documents(safe_business_query)
        total_tables = await db.pos_tables.count_documents(safe_business_query)
        total_kitchen_tickets = await db.pos_kitchen_kot.count_documents(safe_business_query)
        revenue_rows = await db.pos_bills.aggregate([
            {"$match": safe_business_query},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]).to_list(1)
        total_revenue = revenue_rows[0]["total"] if revenue_rows else 0
        recent_logs = await db.audit_logs.find({"business_id": business_id}, {"_id": 0}).sort("created_at", -1).limit(10).to_list(10)
        total_businesses = 1
        total_integrations = await db.integrations.count_documents({"business_id": business_id})
    elif user["role"] != "platform_admin":
        allowed_business_ids = user.get("business_ids", [])
        scoped_query = {"business_id": {"$in": allowed_business_ids}, "tenant_scope_status": {"$ne": "quarantined"}}
        total_outlets = await db.outlets.count_documents({**scoped_query, "status": "active"})
        total_users = await db.users.count_documents({"business_ids": {"$in": allowed_business_ids}})
        total_modules = await db.business_modules.count_documents({"business_id": {"$in": allowed_business_ids}, "enabled": True})
        total_flags = await db.feature_flags.count_documents(scoped_query)
        total_products = await db.products.count_documents(scoped_query)
        total_orders = await db.pos_sales_orders.count_documents(scoped_query)
        total_bills = await db.pos_bills.count_documents(scoped_query)
        total_inventory_items = await db.pos_inventory_admin.count_documents(scoped_query)
        total_staff_records = await db.pos_staff_shifts.count_documents(scoped_query)
        total_tables = await db.pos_tables.count_documents(scoped_query)
        total_kitchen_tickets = await db.pos_kitchen_kot.count_documents(scoped_query)
        revenue_rows = await db.pos_bills.aggregate([
            {"$match": scoped_query},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]).to_list(1)
        total_revenue = revenue_rows[0]["total"] if revenue_rows else 0
        recent_logs = await db.audit_logs.find(scoped_query, {"_id": 0}).sort("created_at", -1).limit(10).to_list(10)
        total_businesses = await db.businesses.count_documents({"id": {"$in": allowed_business_ids}, "status": "active"})
        total_integrations = await db.integrations.count_documents(scoped_query)
    else:
        safe_all_query = {"tenant_scope_status": {"$ne": "quarantined"}}
        total_outlets = await db.outlets.count_documents({**safe_all_query, "status": "active"})
        total_users = await db.users.count_documents({})
        total_modules = await db.modules.count_documents({})
        total_flags = await db.feature_flags.count_documents({})
        total_products = await db.products.count_documents(safe_all_query)
        total_orders = await db.pos_sales_orders.count_documents(safe_all_query)
        total_bills = await db.pos_bills.count_documents(safe_all_query)
        total_inventory_items = await db.pos_inventory_admin.count_documents(safe_all_query)
        total_staff_records = await db.pos_staff_shifts.count_documents(safe_all_query)
        total_tables = await db.pos_tables.count_documents(safe_all_query)
        total_kitchen_tickets = await db.pos_kitchen_kot.count_documents(safe_all_query)
        revenue_rows = await db.pos_bills.aggregate([
            {"$match": safe_all_query},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]).to_list(1)
        total_revenue = revenue_rows[0]["total"] if revenue_rows else 0
        recent_logs = await db.audit_logs.find({}, {"_id": 0}).sort("created_at", -1).limit(10).to_list(10)
        total_businesses = await db.businesses.count_documents({"status": "active"})
        total_integrations = await db.integrations.count_documents({})
    return {
        "total_businesses": total_businesses,
        "total_outlets": total_outlets,
        "total_users": total_users,
        "active_modules": total_modules,
        "total_feature_flags": total_flags,
        "total_integrations": total_integrations,
        "total_products": total_products,
        "total_orders": total_orders,
        "total_bills": total_bills,
        "total_inventory_items": total_inventory_items,
        "total_staff_records": total_staff_records,
        "total_tables": total_tables,
        "total_kitchen_tickets": total_kitchen_tickets,
        "total_revenue": total_revenue,
        "recent_activity": recent_logs,
    }


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
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
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
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.plans.update_one({"id": plan_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Plan not found")
    await create_audit_log(None, user["id"], user["email"], "updated", "plan", plan_id, update_data)
    return await db.plans.find_one({"id": plan_id}, {"_id": 0})

@plan_router.delete("/{plan_id}")
async def delete_plan(plan_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
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
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
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
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await validate_business_access(user, data.business_id)
    plan = await db.plans.find_one({"id": data.plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    existing = await db.subscriptions.find_one({"business_id": data.business_id})
    if existing:
        raise HTTPException(status_code=400, detail="Business already has a subscription. Update it instead.")
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
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    sub = await db.subscriptions.find_one({"id": sub_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await validate_business_access(user, sub.get("business_id"))
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
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
    await create_audit_log(sub["business_id"], user["id"], user["email"], "updated", "subscription", sub_id, update_data)
    return await db.subscriptions.find_one({"id": sub_id}, {"_id": 0})

@subscription_router.post("/{sub_id}/cancel")
async def cancel_subscription(sub_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] not in ["platform_admin", "business_owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    sub = await db.subscriptions.find_one({"id": sub_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await validate_business_access(user, sub.get("business_id"))
    now_ts = datetime.now(timezone.utc).isoformat()
    await db.subscriptions.update_one({"id": sub_id}, {"$set": {"status": "cancelled", "cancelled_at": now_ts, "updated_at": now_ts}})
    await record_subscription_event(sub["business_id"], "SUBSCRIPTION_CANCELLED", user, {})
    await create_audit_log(sub["business_id"], user["id"], user["email"], "cancelled", "subscription", sub_id)
    return {"message": "Subscription cancelled"}

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
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
    await validate_business_access(user, data.business_id)
    addon = await db.addon_catalog.find_one({"id": data.addon_id}, {"_id": 0})
    if not addon:
        raise HTTPException(status_code=404, detail="Add-on not found")
    doc = {"id": str(ObjectId()), "business_id": data.business_id, "addon_id": data.addon_id, "addon_code": addon["code"], "quantity": max(1, int(data.quantity or 1)), "status": data.status, "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.business_addons.insert_one(doc)
    await record_subscription_event(data.business_id, "ADDON_ADDED", user, {"addon_code": addon["code"], "quantity": doc["quantity"]})
    await create_audit_log(data.business_id, user["id"], user["email"], "created", "business_addon", doc["id"], {"addon": addon["code"]})
    return doc

@subscription_router.post("/entitlement-overrides")
async def upsert_entitlement_override(data: EntitlementOverrideCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
    await validate_business_access(user, data.business_id)
    await db.business_entitlement_overrides.update_one(
        {"business_id": data.business_id, "feature_code": data.feature_code},
        {"$set": {"enabled": data.enabled, "reason": data.reason, "updated_by": user["id"], "updated_at": datetime.now(timezone.utc).isoformat()}, "$setOnInsert": {"id": str(ObjectId()), "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    await record_subscription_event(data.business_id, "ENTITLEMENT_OVERRIDDEN", user, {"feature_code": data.feature_code, "enabled": data.enabled})
    return {"message": "Entitlement override saved"}

@subscription_router.post("/limit-overrides")
async def upsert_limit_override(data: LimitOverrideCreate, request: Request):
    user = await get_current_user(request)
    if user["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
    await validate_business_access(user, data.business_id)
    await db.business_limit_overrides.update_one(
        {"business_id": data.business_id, "limit_code": data.limit_code},
        {"$set": {"value": data.value, "reason": data.reason, "updated_by": user["id"], "updated_at": datetime.now(timezone.utc).isoformat()}, "$setOnInsert": {"id": str(ObjectId()), "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    await record_subscription_event(data.business_id, "LIMIT_OVERRIDDEN", user, {"limit_code": data.limit_code, "value": data.value})
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
}

def pos_resource_config(resource: str) -> dict:
    config = POS_ADMIN_RESOURCES.get(resource)
    if not config:
        raise HTTPException(status_code=404, detail="POS admin resource not found")
    return config

async def pos_business_filter(user: dict, business_id: Optional[str]) -> dict:
    query = {"tenant_scope_status": {"$ne": "quarantined"}}
    if user["role"] == "platform_admin":
        if business_id:
            await validate_business_access(user, business_id)
            query["business_id"] = business_id
        return query
    allowed = user.get("business_ids", [])
    if business_id:
        if business_id not in allowed:
            raise HTTPException(status_code=403, detail="Business access denied")
        query["business_id"] = business_id
    else:
        query["business_id"] = {"$in": allowed}
    return query

async def validate_pos_admin_business(user: dict, business_id: Optional[str]):
    await validate_business_access(user, business_id)

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
        module_slug = POS_RESOURCE_MODULES.get(key)
        enabled = True
        if business_id and module_slug:
            module_row = await ensure_business_module_row(business_id, module_slug)
            enabled = bool(module_row.get("enabled", False))
        if enabled or not business_id:
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
):
    config = pos_resource_config(resource)
    user = await get_current_user(request)
    await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES.get(resource))
    query = await pos_business_filter(user, business_id)
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
            {"title": {"$regex": search, "$options": "i"}},
            {"category": {"$regex": search, "$options": "i"}},
            {"owner_name": {"$regex": search, "$options": "i"}},
            {"contact": {"$regex": search, "$options": "i"}},
        ]
    if date_from or date_to:
        created = {}
        if date_from:
            created["$gte"] = f"{date_from}T00:00:00"
        if date_to:
            created["$lte"] = f"{date_to}T23:59:59"
        query["created_at"] = created

    collection = db[config["collection"]]
    if business_id and bridge_resource and await collection.count_documents(query) == 0:
        try:
            await sync_pos_bridge_resource_for_system(bridge_resource, business_id, user)
        except HTTPException as exc:
            raise HTTPException(status_code=exc.status_code, detail={
                "code": f"POS_{resource.upper().replace('-', '_')}_SYNC_FAILED",
                "message": exc.detail,
                "detail": f"This business is linked to POS, but AdminCore could not load POS {config['label']}.",
            }) from exc
    records = await collection.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    total = await collection.count_documents(query)
    status_counts = []
    for status_name in config["statuses"]:
        status_counts.append({"status": status_name, "count": await collection.count_documents({**query, "status": status_name})})
    amount_total = 0
    for row in records:
        try:
            amount_total += float(row.get("amount") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "resource": resource,
        "label": config["label"],
        "statuses": config["statuses"],
        "records": await decorate_pos_records(records),
        "summary": {"total": total, "amount_total": round(amount_total, 2), "status_counts": status_counts},
    }

@pos_admin_router.get("/payments/report")
async def get_pos_payment_report(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES["payments"])
    if business_id and await is_pos_connected_business(business_id):
        await cleanup_mismatched_pos_imports("payments", business_id)
    query = await pos_business_filter(user, business_id)
    rows = await db.pos_payments.find(query, {"_id": 0}).to_list(1000)
    by_method = {}
    by_status = {}
    by_refund = {}
    total_amount = 0
    for row in rows:
        method = row.get("payment_method") or row.get("category") or "manual"
        status = row.get("status") or row.get("payment_status") or "pending"
        refund = row.get("refund_status") or "none"
        amount = float(row.get("amount") or 0)
        total_amount += amount
        by_method[method] = by_method.get(method, 0) + amount
        by_status[status] = by_status.get(status, 0) + 1
        by_refund[refund] = by_refund.get(refund, 0) + 1
    return {
        "total_payments": len(rows),
        "total_amount": round(total_amount, 2),
        "by_method": [{"method": key, "amount": round(value, 2)} for key, value in by_method.items()],
        "by_status": [{"status": key, "count": value} for key, value in by_status.items()],
        "by_refund": [{"refund_status": key, "count": value} for key, value in by_refund.items()],
    }

@pos_admin_router.get("/inventory/{record_id}/movements")
async def get_inventory_movements(record_id: str, request: Request):
    user = await get_current_user(request)
    item = await db.pos_inventory_admin.find_one({"id": record_id}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    await validate_pos_admin_business(user, item.get("business_id"))
    await require_business_module_enabled(item.get("business_id"), POS_RESOURCE_MODULES["inventory"])
    return await db.pos_inventory_movements.find({"inventory_id": record_id}, {"_id": 0}).sort("created_at", -1).to_list(200)

@pos_admin_router.get("/customers/{record_id}/order-history")
async def get_customer_order_history(record_id: str, request: Request):
    user = await get_current_user(request)
    customer = await db.pos_customers.find_one({"id": record_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    await validate_pos_admin_business(user, customer.get("business_id"))
    await require_business_module_enabled(customer.get("business_id"), POS_RESOURCE_MODULES["customers"])
    saved_history = customer.get("order_history") or []
    matching_orders = await db.pos_sales_orders.find({
        "business_id": customer.get("business_id"),
        "$or": [
            {"owner_name": customer.get("title")},
            {"phone": customer.get("phone")},
            {"email": customer.get("email")},
            {"contact": customer.get("phone")},
            {"contact": customer.get("email")},
        ],
    }, {"_id": 0}).sort("created_at", -1).to_list(100)
    return {"saved_history": saved_history, "orders": matching_orders}

@pos_admin_router.get("/kitchen-kot/performance")
async def get_kitchen_performance(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES["kitchen-kot"])
    if business_id and await is_pos_connected_business(business_id):
        await cleanup_mismatched_pos_imports("kitchen-tickets", business_id)
    query = await pos_business_filter(user, business_id)
    tickets = await db.pos_kitchen_kot.find(query, {"_id": 0}).to_list(1000)
    status_counts = {}
    chef_counts = {}
    item_status_counts = {}
    for ticket in tickets:
        status = ticket.get("status") or "pending"
        status_counts[status] = status_counts.get(status, 0) + 1
        chef = ticket.get("chef_name") or ticket.get("owner_name") or "Unassigned"
        chef_counts[chef] = chef_counts.get(chef, 0) + 1
        for item_status in (ticket.get("item_statuses") or {}).values():
            item_status_counts[item_status] = item_status_counts.get(item_status, 0) + 1
    return {
        "total_tickets": len(tickets),
        "by_status": [{"status": key, "count": value} for key, value in status_counts.items()],
        "by_chef": [{"chef": key, "tickets": value} for key, value in chef_counts.items()],
        "by_item_status": [{"status": key, "count": value} for key, value in item_status_counts.items()],
    }

@pos_admin_router.get("/reports-analytics/summary")
async def get_reports_summary(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES["reports-analytics"])
    if business_id and await is_pos_connected_business(business_id):
        for bridge_resource in ["orders", "bills", "payments", "inventory", "staff-shifts", "tables", "kitchen-tickets", "products", "outlets"]:
            await cleanup_mismatched_pos_imports(bridge_resource, business_id)
    query = await pos_business_filter(user, business_id)
    sales = await db.pos_sales_orders.find(query, {"_id": 0}).to_list(1000)
    payments = await db.pos_payments.find(query, {"_id": 0}).to_list(1000)
    inventory = await db.pos_inventory_admin.find(query, {"_id": 0}).to_list(1000)
    staff = await db.pos_staff_shifts.find(query, {"_id": 0}).to_list(1000)
    taxes = await db.pos_taxes_charges.find(query, {"_id": 0}).to_list(1000)
    products = await db.products.find(query, {"_id": 0}).to_list(1000)
    outlets = await db.outlets.find(query, {"_id": 0}).to_list(1000)
    return {
        "sales": {"count": len(sales), "total": round(sum(float(row.get("amount") or 0) for row in sales), 2)},
        "products": {"count": len(products), "active": len([row for row in products if row.get("active") is not False])},
        "inventory": {"count": len(inventory), "low_stock": len([row for row in inventory if row.get("status") == "low_stock"])},
        "payments": {"count": len(payments), "total": round(sum(float(row.get("amount") or 0) for row in payments), 2)},
        "staff": {"count": len(staff), "completed": len([row for row in staff if row.get("status") == "completed"])},
        "outlets": {"count": len(outlets), "active": len([row for row in outlets if row.get("status") == "active"])},
        "taxes": {"count": len(taxes), "active": len([row for row in taxes if row.get("status") == "active"])},
    }

def csv_escape(value) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace('"', '""') + '"'

def simple_pdf_bytes(title: str, lines: list[str]) -> bytes:
    safe_lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")[:90] for line in lines[:35]]
    stream_lines = ["BT", "/F1 14 Tf", "50 780 Td", f"({title}) Tj", "/F1 10 Tf"]
    for line in safe_lines:
        stream_lines.append("0 -18 Td")
        stream_lines.append(f"({line}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines)
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        f"5 0 obj << /Length {len(stream.encode('latin-1', 'ignore'))} >> stream\n{stream}\nendstream endobj",
    ]
    body = "%PDF-1.4\n" + "\n".join(objects) + "\n"
    return (body + "trailer << /Root 1 0 R >>\n%%EOF").encode("latin-1", "ignore")

@pos_admin_router.get("/reports-analytics/export")
async def export_reports(
    request: Request,
    business_id: Optional[str] = Query(None),
    report_type: str = Query("sales"),
    format: str = Query("csv"),
):
    user = await get_current_user(request)
    await require_module_for_business_scope(user, business_id, POS_RESOURCE_MODULES["reports-analytics"])
    query = await pos_business_filter(user, business_id)
    source_map = {
        "sales": db.pos_sales_orders,
        "products": db.products,
        "inventory": db.pos_inventory_admin,
        "payments": db.pos_payments,
        "staff": db.pos_staff_shifts,
        "outlets": db.outlets,
        "taxes": db.pos_taxes_charges,
    }
    collection = source_map.get(report_type)
    if not collection:
        raise HTTPException(status_code=400, detail="Unsupported report_type")
    rows = await collection.find(query if report_type not in ["products", "outlets"] or query.get("business_id") else {}, {"_id": 0}).sort("created_at", -1).to_list(1000)
    headers = ["id", "title", "name", "business_id", "outlet_id", "status", "category", "amount", "created_at"]
    if format == "pdf":
        lines = [", ".join([str(row.get("title") or row.get("name") or row.get("id")), str(row.get("status", "")), str(row.get("amount", ""))]) for row in rows]
        return Response(content=simple_pdf_bytes(f"{report_type.title()} Report", lines), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={report_type}-report.pdf"})
    csv = ",".join(headers) + "\n"
    for row in rows:
        csv += ",".join(csv_escape(row.get(header)) for header in headers) + "\n"
    return Response(content=csv, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={report_type}-report.csv"})

@pos_admin_router.get("/{resource}/{record_id}")
async def get_pos_admin_record(resource: str, record_id: str, request: Request):
    config = pos_resource_config(resource)
    user = await get_current_user(request)
    record = await db[config["collection"]].find_one({"id": record_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="POS admin record not found")
    await validate_pos_admin_business(user, record.get("business_id"))
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
    if POS_RESOURCE_MODULES.get(resource) and not data.business_id:
        raise HTTPException(status_code=400, detail="business_id is required")
    await validate_pos_admin_business(user, data.business_id)
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
    collection = db[config["collection"]]
    existing = await collection.find_one({"id": record_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="POS admin record not found")
    await validate_pos_admin_business(user, existing.get("business_id"))
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
    collection = db[config["collection"]]
    existing = await collection.find_one({"id": record_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="POS admin record not found")
    await validate_pos_admin_business(user, existing.get("business_id"))
    await require_business_module_enabled(existing.get("business_id"), POS_RESOURCE_MODULES.get(resource))
    await collection.delete_one({"id": record_id})
    await create_audit_log(existing.get("business_id"), user["id"], user["email"], "deleted", f"pos_admin:{resource}", record_id)
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
    return await db[collection_name].count_documents(query)

@control_center_router.get("/overview")
async def get_control_center_overview(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    base_filter = await pos_business_filter(user, business_id)
    scoped_business_id = base_filter.get("business_id") if isinstance(base_filter.get("business_id"), str) else business_id
    scoped_business_ids = [scoped_business_id] if scoped_business_id else []
    if not scoped_business_id and user["role"] != "platform_admin":
        scoped_business_ids = user.get("business_ids", [])

    async def count_for_control_scope(collection_name: str) -> int:
        if scoped_business_id or user["role"] == "platform_admin":
            return await count_for_business(collection_name, scoped_business_id)
        if collection_name == "businesses":
            return await db.businesses.count_documents({"id": {"$in": scoped_business_ids}})
        if collection_name == "users":
            return await db.users.count_documents({"business_ids": {"$in": scoped_business_ids}})
        if collection_name == "modules":
            return await db.business_modules.count_documents({"business_id": {"$in": scoped_business_ids}, "enabled": True})
        return await db[collection_name].count_documents({"business_id": {"$in": scoped_business_ids}})

    active_sub_query = {"status": {"$in": ["active", "trial"]}}
    if scoped_business_id:
        active_sub_query["business_id"] = scoped_business_id
    elif user["role"] != "platform_admin":
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
            elif user["role"] != "platform_admin":
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
        query = dict(base_filter)
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
    elif user["role"] != "platform_admin":
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
            "users": await count_for_business("users", scoped_business_id),
            "active_subscriptions": len(active_subscriptions),
            "monthly_recurring_revenue": round(mrr, 2),
            "saas_modules": len(SAAS_CONTROL_MODULES),
            "pos_modules": len(POS_ADMIN_RESOURCES),
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
    "bills": {"endpoint": "billing", "endpoint_candidates": ["billing", "bills"], "mode": "pos_admin", "collection": "pos_bills", "pos_resource": "bills", "label": "Bills"},
    "payments": {"endpoint": "billing", "endpoint_candidates": ["billing", "bills", "payments", "billing/payments"], "mode": "pos_admin", "collection": "pos_payments", "pos_resource": "payments", "label": "Payments"},
    "tables": {"endpoint": "sync/export/tables", "endpoint_candidates": ["sync/export/tables", "table-management", "tables"], "mode": "pos_admin", "collection": "pos_tables", "pos_resource": "tables", "label": "Tables"},
    "reservations": {"endpoint": "sync/export/reservations", "endpoint_candidates": ["sync/export/reservations", "reservations", "table-reservations"], "mode": "pos_admin", "collection": "pos_reservations", "pos_resource": "reservations", "label": "Reservations"},
    "customers": {"endpoint": "orders", "endpoint_candidates": ["customers", "crm/customers", "orders", "billing", "bills"], "mode": "pos_admin", "collection": "pos_customers", "pos_resource": "customers", "label": "Customers"},
    "kitchen-tickets": {"endpoint": "kot", "endpoint_candidates": ["kot", "kitchen", "billing"], "mode": "pos_admin", "collection": "pos_kitchen_kot", "pos_resource": "kitchen-kot", "label": "Kitchen Tickets"},
    "inventory": {"endpoint": "sync/export/inventory", "endpoint_candidates": ["sync/export/inventory", "inventory"], "mode": "pos_admin", "collection": "pos_inventory_admin", "pos_resource": "inventory", "label": "Inventory"},
    "staff-shifts": {"endpoint": "sync/export/staff", "endpoint_candidates": ["sync/export/staff", "staff-shifts", "staff", "attendance", "users"], "mode": "pos_admin", "collection": "pos_staff_shifts", "pos_resource": "staff-shifts", "label": "Staff Shifts"},
    "reports": {"endpoint": "dashboard/stats", "mode": "pos_admin", "collection": "pos_reports_analytics", "pos_resource": "reports-analytics", "label": "Reports"},
}

def pos_bridge_resource(resource: str) -> dict:
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
        for key in ["records", "items", "results", "rows", "businesses", "outlets", "products", "orders", "bills", "payments", "tables", "qr_codes", "tickets", "kots", "inventory", "customers", "reservations", "staff", "shifts"]:
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
    if resource == "payments":
        rows = derive_payment_rows(rows)
    elif resource == "customers":
        rows = derive_customer_rows(rows)
    elif resource == "kitchen-tickets":
        rows = derive_kitchen_rows(rows)
    return await rows_with_expected_scope(resource, rows, business_id)

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
    if not actual_business_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POS_TENANT_SCOPE_MISSING",
                "resource": resource,
                "expected_business_id": scope["business_id"],
                "expected_tenant_id": scope["tenant_id"],
                "message": "POS returned data without business_id, so AdminCore refused to import it.",
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
    if not business_id or resource == "businesses":
        return rows
    scope = await expected_pos_scope_for_business(business_id)
    for row in rows:
        if isinstance(row, dict):
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
    ensure_pos_bridge_config()
    config = pos_bridge_resource(resource)
    endpoint_candidates = config.get("endpoint_candidates") or [config["endpoint"]]
    headers = {}
    if POS_CORE_API_KEY:
        headers["Authorization"] = f"Bearer {POS_CORE_API_KEY}"
        headers["x-api-key"] = POS_CORE_API_KEY
    if business_id:
        headers.update(await pos_headers_for_admin_business(business_id))
    request_params = dict(params or {})
    if business_id and resource != "businesses":
        request_params.setdefault("business_id", headers.get("business_id") or headers.get("x-business-id"))
        request_params.setdefault("businessId", headers.get("business_id") or headers.get("x-business-id"))
        request_params.setdefault("tenant_id", headers.get("tenant_id") or headers.get("x-tenant-id"))
        request_params.setdefault("tenantId", headers.get("tenantId") or headers.get("x-tenant-id"))

    def do_request():
        session = requests.Session()
        pos_core_login(session, headers)
        last_response = None
        for index, endpoint in enumerate(endpoint_candidates):
            url = f"{POS_CORE_API_BASE_URL}/api/{endpoint.strip('/')}"
            response = session.get(url, params=request_params, headers=headers, timeout=POS_CORE_REQUEST_TIMEOUT_SECONDS)
            last_response = response
            if response.status_code == 404 and index < len(endpoint_candidates) - 1:
                continue
            if resource == "businesses" and response.status_code == 404:
                products_response = session.get(
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
            return response.json()
        if last_response:
            last_response.raise_for_status()
        return []

    try:
        payload = await run_in_threadpool(do_request)
        await validate_pos_rows_for_business(resource, await prepare_pos_bridge_rows(resource, payload, business_id), business_id)
        return payload
    except requests.RequestException as exc:
        detail = requests_error_detail(exc, f"POS bridge request for {resource}")
        detail.update({
            "code": "POS_BRIDGE_REQUEST_FAILED",
            "resource": resource,
            "endpoint": config.get("endpoint"),
            "tried": endpoint_candidates,
        })
        raise HTTPException(status_code=502, detail=detail) from exc

async def pos_core_session_request(method: str, endpoint: str, json: dict | None = None, params: dict | None = None, extra_headers: Optional[dict] = None):
    ensure_pos_bridge_config()
    headers = {}
    if POS_CORE_API_KEY:
        headers["Authorization"] = f"Bearer {POS_CORE_API_KEY}"
        headers["x-api-key"] = POS_CORE_API_KEY
    if extra_headers:
        headers.update(extra_headers)

    def do_request():
        session = requests.Session()
        pos_core_login(session, headers)
        response = session.request(
            method,
            f"{POS_CORE_API_BASE_URL}/api/{endpoint.strip('/')}",
            json=json,
            params=params or {},
            headers=headers,
            timeout=POS_CORE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    try:
        return await run_in_threadpool(do_request)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=requests_error_detail(exc, f"POS core request for {endpoint}")) from exc

async def pos_headers_for_admin_business(business_id: Optional[str]) -> dict:
    if not business_id:
        return {}
    provisioned = await provision_admin_business_to_pos(business_id)
    if not provisioned:
        return {}
    return {
        "business_id": provisioned["business_id"],
        "x-business-id": provisioned["business_id"],
        "tenant_id": provisioned["tenant_id"],
        "tenantId": provisioned["tenant_id"],
        "x-tenant-id": provisioned["tenant_id"],
    }

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
    existing = await db.outlets.find_one({"business_id": business_id}, {"_id": 0}, sort=[("created_at", 1)])
    if existing:
        link_update = {
            "pos_business_id": resolved_pos_business_id,
            "pos_tenant_id": resolved_pos_tenant_id,
            "updated_at": now_ts,
        }
        await db.outlets.update_one({"id": existing["id"]}, {"$set": link_update})
        existing = {**existing, **link_update}
        if sync_to_pos and POS_CORE_API_BASE_URL:
            try:
                await push_admin_outlet_to_pos(existing, pos_headers={"business_id": resolved_pos_business_id, "x-tenant-id": resolved_pos_tenant_id})
                existing = await db.outlets.find_one({"id": existing["id"]}, {"_id": 0})
            except HTTPException as exc:
                logger.warning("Could not sync existing default outlet to POS: %s", exc.detail)
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
    await db.outlets.insert_one(outlet_doc)
    if user:
        await create_audit_log(business_id, user["id"], user["email"], "created", "outlet", outlet_doc["id"], {"name": "Main Outlet", "default": True})
    if sync_to_pos and POS_CORE_API_BASE_URL:
        try:
            await push_admin_outlet_to_pos(outlet_doc, pos_headers={"business_id": resolved_pos_business_id, "x-tenant-id": resolved_pos_tenant_id})
            outlet_doc = await db.outlets.find_one({"id": outlet_doc["id"]}, {"_id": 0})
        except HTTPException as exc:
            logger.warning("Could not sync default outlet to POS: %s", exc.detail)
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
    rows = normalize_pos_bridge_rows(await pos_core_session_request("GET", "businesses"))
    existing = next(
        (
            row for row in rows
            if str(row.get("id") or row.get("business_id") or row.get("pos_business_id") or "") == str(pos_business_id)
            or (row.get("name") or "").strip().lower() == (payload["name"] or "").strip().lower()
        ),
        None,
    )
    if existing:
        existing_id = str(existing.get("id") or existing.get("business_id") or pos_business_id)
        pos_business_id = existing_id
        tenant_id = existing.get("tenantId") or existing.get("tenant_id") or tenant_id
        try:
            result = await pos_core_session_request("PUT", f"businesses/{existing_id}", json=payload)
        except HTTPException as exc:
            logger.warning("POS business exists but could not be updated; linking existing business: %s", exc.detail)
            result = {"linked_existing": True, "data": existing}
    else:
        result = await pos_core_session_request("POST", "businesses", json=payload)
        created = result.get("data") if isinstance(result, dict) and "data" in result else result
        if isinstance(created, dict):
            pos_business_id = str(created.get("id") or created.get("business_id") or pos_business_id)
            tenant_id = created.get("tenantId") or created.get("tenant_id") or tenant_id
    await db.businesses.update_one(
        {"id": business_id},
        {"$set": {"pos_external_id": pos_business_id, "pos_tenant_id": tenant_id, "pos_synced": True, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    await ensure_default_outlet_for_business(business_id, sync_to_pos=True, pos_business_id=pos_business_id, pos_tenant_id=tenant_id)
    return {"business_id": pos_business_id, "tenant_id": tenant_id, "result": result}

async def push_admin_user_to_pos(user_doc: dict, password: Optional[str] = None, allow_generated_password: bool = False):
    business_ids = user_doc.get("business_ids") or []
    if not POS_CORE_API_BASE_URL or not business_ids:
        return None
    if not user_doc.get("email"):
        return None
    business_id = business_ids[0]
    pos_headers = await pos_headers_for_admin_business(business_id)
    business = await db.businesses.find_one({"id": business_id}, {"_id": 0})
    outlet = await ensure_default_outlet_for_business(
        business_id,
        sync_to_pos=True,
        pos_business_id=(business or {}).get("pos_external_id") or pos_headers.get("business_id"),
        pos_tenant_id=(business or {}).get("pos_tenant_id") or pos_headers.get("x-tenant-id"),
    )
    pos_business_id = (business or {}).get("pos_external_id") or pos_headers.get("business_id") or business_id
    pos_tenant_id = (business or {}).get("pos_tenant_id") or pos_headers.get("x-tenant-id") or f"admincore-{business_id}"
    pos_outlet_id = (outlet or {}).get("pos_external_id") or (outlet or {}).get("id")
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
    if password:
        payload["password"] = password
    else:
        if not allow_generated_password:
            raise HTTPException(status_code=400, detail="Set a new password before syncing this user to POS")
        payload["password"] = secrets.token_urlsafe(10)

    result = await pos_core_session_request("POST", "admincore/staff", json=payload, extra_headers=pos_headers)
    created = result.get("data") if isinstance(result, dict) and "data" in result else result
    pos_user_id = created.get("id") if isinstance(created, dict) else None
    created_business_id = str((created or {}).get("business_id") or (created or {}).get("businessId") or "")
    if created_business_id and created_business_id != str(pos_business_id):
        raise HTTPException(status_code=502, detail={
            "code": "POS_USER_BUSINESS_MISMATCH",
            "message": "POS linked the owner user under a different business than AdminCore requested.",
            "email": user_doc["email"],
            "created_business_id": created_business_id,
            "target_business_id": pos_business_id,
        })
    if pos_user_id:
        await db.users.update_one(
            {"id": user_doc["id"]},
            {"$set": {
                "pos_external_id": pos_user_id,
                "pos_business_id": pos_business_id,
                "pos_tenant_id": pos_tenant_id,
                "pos_assigned_outlet_ids": [pos_outlet_id] if pos_outlet_id else [],
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
    product_rows = normalize_pos_bridge_rows(await pos_core_session_request("GET", "products", extra_headers=pos_headers))
    existing = next(
        (
            row for row in product_rows
            if row.get("id") == product_doc.get("pos_external_id")
            or (row.get("name") or "").strip().lower() == (product_doc.get("name") or "").strip().lower()
        ),
        None,
    )
    payload = {
        "name": product_doc.get("name") or "Product",
        "price": product_doc.get("price") or 0,
        "stock": product_doc.get("stock") or 0,
        "category": product_doc.get("category") or "General",
        "active": product_doc.get("active", True),
    }
    if existing:
        result = await pos_core_session_request("PUT", f"products/{existing['id']}", json=payload, extra_headers=pos_headers)
        pos_product_id = existing["id"]
    else:
        result = await pos_core_session_request("POST", "products", json=payload, extra_headers=pos_headers)
        created = result.get("data") if isinstance(result, dict) and "data" in result else result
        pos_product_id = created.get("id") if isinstance(created, dict) else None
    if pos_product_id:
        await db.products.update_one(
            {"id": product_doc["id"]},
            {"$set": {"pos_external_id": pos_product_id, "pos_synced": True, "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    return result

async def delete_admin_product_from_pos(product_doc: dict):
    if not POS_CORE_API_BASE_URL or not product_doc.get("business_id") or not product_doc.get("pos_external_id"):
        return None
    pos_headers = await pos_headers_for_admin_business(product_doc["business_id"])
    return await pos_core_session_request("DELETE", f"products/{product_doc['pos_external_id']}", extra_headers=pos_headers)

async def push_admin_outlet_to_pos(outlet_doc: dict, pos_headers: Optional[dict] = None):
    business_id = outlet_doc.get("business_id")
    if not POS_CORE_API_BASE_URL or not business_id:
        return None
    pos_headers = pos_headers or await pos_headers_for_admin_business(business_id)
    outlet_rows = normalize_pos_bridge_rows(await pos_core_session_request("GET", "outlets", extra_headers=pos_headers))
    outlet_name = (outlet_doc.get("name") or "").strip().lower()
    outlet_code = (outlet_doc.get("code") or "").strip().lower()
    existing = next(
        (
            row for row in outlet_rows
            if row.get("id") == outlet_doc.get("pos_external_id")
            or ((row.get("name") or "").strip().lower() == outlet_name and outlet_name)
            or ((row.get("code") or "").strip().lower() == outlet_code and outlet_code)
        ),
        None,
    )
    payload = {
        "name": outlet_doc.get("name") or "Outlet",
        "code": outlet_doc.get("code") or make_outlet_code("OUT"),
        "location": outlet_doc.get("address") or "",
        "address": outlet_doc.get("address") or "",
        "manager_name": outlet_doc.get("manager_name") or "",
        "managerName": outlet_doc.get("manager_name") or "",
        "phone": outlet_doc.get("phone") or "",
        "status": outlet_doc.get("status") or "active",
        "business_id": outlet_doc.get("pos_business_id") or pos_headers.get("business_id") or business_id,
        "tenantId": outlet_doc.get("pos_tenant_id") or pos_headers.get("x-tenant-id") or "",
        "tenant_id": outlet_doc.get("pos_tenant_id") or pos_headers.get("x-tenant-id") or "",
    }
    if existing:
        result = await pos_core_session_request("PUT", f"outlets/{existing['id']}", json=payload, extra_headers=pos_headers)
        pos_outlet_id = existing["id"]
    else:
        result = await pos_core_session_request("POST", "outlets", json=payload, extra_headers=pos_headers)
        created = result.get("data") if isinstance(result, dict) and "data" in result else result
        pos_outlet_id = created.get("id") if isinstance(created, dict) else None
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
    if not POS_CORE_API_BASE_URL or not outlet_doc.get("business_id") or not outlet_doc.get("pos_external_id"):
        return None
    pos_headers = await pos_headers_for_admin_business(outlet_doc["business_id"])
    return await pos_core_session_request("DELETE", f"outlets/{outlet_doc['pos_external_id']}", extra_headers=pos_headers)

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

    if candidate not in configured_keys:
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
    name = title_for(row, "POS Business")
    doc = {
        "name": name,
        "slug": existing.get("slug") if existing else await unique_business_slug(row.get("slug") or name),
        "type": row.get("type") or "restaurant",
        "plan": row.get("plan") or "starter",
        "status": row.get("status") or "active",
        "branding": row.get("branding") or {"primary_color": "#0055FF", "business_name": name},
        "pos_external_id": external_id,
        "pos_synced": True,
        "updated_at": now_ts,
    }
    if existing:
        await db.businesses.update_one({"id": existing["id"]}, {"$set": doc})
        await ensure_default_outlet_for_business(
            existing["id"],
            user=user,
            sync_to_pos=bool(POS_CORE_API_BASE_URL),
            pos_business_id=external_id,
            pos_tenant_id=row.get("tenantId") or row.get("tenant_id") or existing.get("pos_tenant_id"),
        )
        return existing["id"]
    doc.update({"id": str(ObjectId()), "owner_id": user["id"], "created_at": now_ts})
    await db.businesses.insert_one(doc)
    if user.get("role") != "platform_admin":
        await db.users.update_one({"id": user["id"]}, {"$addToSet": {"business_ids": doc["id"]}})
    await seed_business_defaults(doc["id"])
    await ensure_default_outlet_for_business(
        doc["id"],
        user=user,
        sync_to_pos=bool(POS_CORE_API_BASE_URL),
        pos_business_id=external_id,
        pos_tenant_id=row.get("tenantId") or row.get("tenant_id"),
    )
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
    match_terms = [{"pos_external_id": external_id}, {"external_id": external_id}, {"name": outlet_name}]
    if outlet_code:
        match_terms.append({"code": outlet_code})
    existing = await db.outlets.find_one(
        {
            "business_id": business_id,
            "$or": match_terms,
        },
        {"_id": 0},
    )
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
            "$or": [
                {"pos_external_id": external_id},
                {"external_id": external_id},
                {"name": product_name},
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
        "pos_external_id": external_id,
        "pos_business_id": row.get("business_id") or row.get("businessId") or "",
        "pos_tenant_id": row.get("tenantId") or row.get("tenant_id") or "",
        "pos_synced": True,
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
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    role = normalize_pos_staff_role(row.get("role"))
    status = "active" if row.get("active", True) is not False and str(row.get("status", "active")).lower() != "inactive" else "inactive"
    update = {
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
        await db.users.update_one({"id": existing["id"]}, {"$set": update, "$addToSet": {"business_ids": business_id}})
        return existing["id"]
    doc = {
        "id": str(ObjectId()),
        "email": email,
        "password_hash": hash_password(known_pos_staff_password(email)),
        "business_ids": [business_id],
        "created_at": now_ts,
        **update,
    }
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
    if user.get("role") != "platform_admin":
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
    await assert_pos_row_scope(config.get("pos_resource") or config["label"], row, business_id)
    external_id = external_id_for(row)
    collection = db[config["collection"]]
    target_business_id = business_id or row.get("business_id") or row.get("businessId") or ""
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
        "amount": float(row.get("total") or row.get("amount") or row.get("guest_count") or row.get("party_size") or row.get("stock") or row.get("count") or 0),
        "due_date": row.get("due_date") or row.get("reservation_time") or row.get("reserved_at") or row.get("booking_date") or row.get("requiredBy") or "",
        "notes": row.get("notes") or row.get("special_requests") or "",
        "metadata": row,
        "resource": config.get("pos_resource") or config["label"],
        "pos_external_id": external_id,
        "pos_business_id": row.get("business_id") or row.get("businessId") or "",
        "pos_tenant_id": row.get("tenantId") or row.get("tenant_id") or "",
        "pos_synced": True,
        "tenant_scope_status": "verified",
        "created_by": user["id"],
        "updated_at": now_ts,
    }
    if existing:
        await collection.update_one({"id": existing["id"]}, {"$set": doc})
        return existing["id"]
    doc.update({"id": str(ObjectId()), "created_at": now_ts})
    await collection.insert_one(doc)
    return doc["id"]

@pos_bridge_router.get("/config")
async def get_pos_bridge_config(request: Request):
    await get_current_user(request)
    return {
        "configured": bool(POS_CORE_API_BASE_URL),
        "base_url": POS_CORE_API_BASE_URL,
        "api_key_configured": bool(POS_CORE_API_KEY),
        "owner_login_configured": bool(POS_CORE_OWNER_EMAIL and POS_CORE_OWNER_PASSWORD),
        "env": {
            "base_url": "POS_CORE_API_BASE_URL",
            "api_key": "POS_CORE_API_KEY",
            "owner_email": "POS_CORE_OWNER_EMAIL",
            "owner_password": "POS_CORE_OWNER_PASSWORD",
        },
    }

@pos_bridge_router.get("/resources")
async def list_pos_bridge_resources(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    await validate_pos_admin_business(user, business_id)
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["integrations"])
    rows = []
    for key, config in POS_BRIDGE_RESOURCES.items():
        count_query = {}
        sync_query = {"resource": key, "business_id": business_id or ""}
        if business_id:
            if config["collection"] == "businesses":
                count_query = {"id": business_id}
            elif config["collection"] == "users":
                count_query = {"business_ids": business_id}
            else:
                count_query = {"business_id": business_id}
        elif user.get("role") != "platform_admin":
            sync_query["business_id"] = {"$in": user.get("business_ids", [])}
            if config["collection"] == "businesses":
                count_query = {"id": {"$in": user.get("business_ids", [])}}
            elif config["collection"] == "users":
                count_query = {"business_ids": {"$in": user.get("business_ids", [])}}
            else:
                count_query = {"business_id": {"$in": user.get("business_ids", [])}}
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
    user = await get_current_user(request)
    if not business_id and user.get("role") != "platform_admin":
        raise HTTPException(status_code=400, detail="business_id is required for POS bridge live data")
    await validate_pos_admin_business(user, business_id)
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["integrations"])
    params = {}
    payload = await pos_bridge_request(resource, params, business_id=business_id)
    rows = await validate_pos_rows_for_business(resource, await prepare_pos_bridge_rows(resource, payload, business_id), business_id)
    return {"resource": resource, "rows": rows, "raw": payload}

@pos_bridge_router.post("/sync/{resource}")
async def sync_pos_bridge_resource(resource: str, request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    if not business_id and user.get("role") != "platform_admin":
        raise HTTPException(status_code=400, detail="business_id is required for POS bridge sync")
    await validate_pos_admin_business(user, business_id)
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["integrations"])
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
    status = "success" if not errors else ("partial" if synced else "failed")
    sync_run = await record_pos_bridge_sync_run(resource, local_business_id, user, status, len(synced), len(errors), errors, now_ts)
    await create_audit_log(local_business_id, user["id"], user["email"], "synced" if status != "failed" else "sync_failed", f"pos_bridge:{resource}", None, {"synced": len(synced), "errors": len(errors), "status": status})
    return {"resource": resource, "synced": synced, "errors": errors, "count": len(synced), "error_count": len(errors), "status": status, "sync_run": sync_run}

async def sync_pos_bridge_resource_for_system(resource: str, business_id: Optional[str], user: dict):
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
    if resource not in POS_BRIDGE_RESOURCES:
        raise HTTPException(status_code=400, detail=f"Unsupported POS bridge resource: {resource}")

    user = await get_pos_bridge_system_user()
    now_ts = datetime.now(timezone.utc).isoformat()
    business_id = payload.get("admincore_business_id") or payload.get("local_business_id")
    if not business_id:
        business_id = await local_business_id_for_pos_row(
            {
                "business_id": payload.get("business_id"),
                "businessId": payload.get("business_id"),
                "tenant_id": payload.get("tenant_id"),
                "tenantId": payload.get("tenant_id"),
            },
            user,
            now_ts,
        )

    result = await sync_pos_bridge_resource_for_system(resource, business_id, user)
    return {
        "accepted": True,
        "message": "POS change notification received and synced",
        "resource": resource,
        "business_id": business_id,
        "result": result,
    }

@pos_bridge_router.post("/sync-all")
async def sync_all_pos_bridge_resources(request: Request, business_id: Optional[str] = Query(None)):
    user = await get_current_user(request)
    if not business_id and user.get("role") != "platform_admin":
        raise HTTPException(status_code=400, detail="business_id is required for POS bridge sync")
    await validate_pos_admin_business(user, business_id)
    await require_module_for_business_scope(user, business_id, CORE_FEATURE_MODULES["integrations"])

    async def sync_one(resource: str):
        try:
            result = await asyncio.wait_for(
                sync_pos_bridge_resource_for_system(resource, business_id, user),
                timeout=POS_BRIDGE_RESOURCE_TIMEOUT_SECONDS,
            )
            return resource, result
        except asyncio.TimeoutError:
            now_ts = datetime.now(timezone.utc).isoformat()
            errors = [{
                "resource": resource,
                "reason": f"Timed out after {POS_BRIDGE_RESOURCE_TIMEOUT_SECONDS} seconds",
                "detail": {
                    "code": "POS_BRIDGE_RESOURCE_TIMEOUT",
                    "resource": resource,
                    "timeout_seconds": POS_BRIDGE_RESOURCE_TIMEOUT_SECONDS,
                },
            }]
            sync_run = await record_pos_bridge_sync_run(resource, business_id, user, "failed", 0, 1, errors, now_ts)
            return resource, {"resource": resource, "status": "failed", "count": 0, "error_count": 1, "errors": errors, "sync_run": sync_run}
        except HTTPException as exc:
            detail = compact_bridge_error_detail(exc.detail)
            return resource, {"resource": resource, "status": "failed", "count": 0, "error_count": 1, "errors": [{"reason": bridge_error_message(detail), "detail": detail}]}
        except Exception as exc:
            return resource, {"resource": resource, "status": "failed", "count": 0, "error_count": 1, "errors": [{"reason": str(exc)}]}

    results = dict(await asyncio.gather(*(sync_one(resource) for resource in POS_BRIDGE_RESOURCES)))
    return {"results": results}


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

async def seed_data():
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@admin.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")

    existing_admin = await db.users.find_one({"email": admin_email})
    if existing_admin:
        if not verify_password(admin_password, existing_admin["password_hash"]):
            await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hash_password(admin_password)}})
        logger.info("Admin user verified, skipping seed")
        return

    logger.info("Seeding database with demo data...")
    now = datetime.now(timezone.utc).isoformat()

    # Seed system modules
    for mod in SYSTEM_MODULES:
        mod_doc = {**mod, "id": str(ObjectId()), "created_at": now}
        await db.modules.insert_one(mod_doc)

    # Create businesses
    biz1_id, biz2_id, biz3_id = str(ObjectId()), str(ObjectId()), str(ObjectId())
    businesses = [
        {"id": biz1_id, "name": "Sunrise Restaurant", "slug": "sunrise-restaurant", "type": "restaurant", "plan": "pro", "status": "active", "branding": {"primary_color": "#FF6B35", "business_name": "Sunrise Restaurant"}, "owner_id": "", "created_at": now, "updated_at": now},
        {"id": biz2_id, "name": "Urban Wellness Cafe", "slug": "urban-wellness-cafe", "type": "cafe", "plan": "starter", "status": "active", "branding": {"primary_color": "#2D9B83", "business_name": "Urban Wellness Cafe"}, "owner_id": "", "created_at": now, "updated_at": now},
        {"id": biz3_id, "name": "Metro Retail Hub", "slug": "metro-retail-hub", "type": "retail", "plan": "enterprise", "status": "active", "branding": {"primary_color": "#6C5CE7", "business_name": "Metro Retail Hub"}, "owner_id": "", "created_at": now, "updated_at": now},
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
    biz_plan_map = {biz1_id: "pro", biz2_id: "starter", biz3_id: "enterprise"}
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
                sync_to_pos=bool(POS_CORE_API_BASE_URL),
                pos_business_id=business.get("pos_external_id") or business["id"],
                pos_tenant_id=business.get("pos_tenant_id") or f"admincore-{business['id']}",
            )

@app.on_event("startup")
async def startup():
    await repair_legacy_outlet_indexes()
    await seed_data()
    await sync_system_modules()
    await repair_missing_pos_default_outlets()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
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
    if POS_PROVISIONING_WORKER_ENABLED:
        asyncio.create_task(pos_provisioning_worker())
    logger.info("Database indexes created")


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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===================================================================
# SHUTDOWN
# ===================================================================
@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
