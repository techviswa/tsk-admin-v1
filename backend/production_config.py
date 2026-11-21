"""Validate deployment-critical settings without hiding the API behind startup crashes."""
import re
from urllib.parse import urlparse


def validate_production_config(environ, *, raise_on_error=True):
    production = any(environ.get(key, "").lower() == "production" for key in ["ENVIRONMENT", "APP_ENV", "NODE_ENV"])
    production = production or environ.get("RENDER", "").lower() == "true"
    if not production:
        return {"production": False, "ok": True, "errors": []}
    errors = []
    missing = [key for key in ["MONGO_URL", "DB_NAME", "JWT_SECRET", "FRONTEND_URL", "POS_CORE_API_BASE_URL", "POS_CORE_API_KEY"] if not environ.get(key, "").strip()]
    if missing:
        errors.append("Missing production settings: " + ", ".join(missing))
    if environ.get("JWT_SECRET") and len(environ["JWT_SECRET"]) < 32:
        errors.append("JWT_SECRET must contain at least 32 characters in production")
    for key in ["JWT_SECRET", "POS_CORE_API_KEY"]:
        value = environ.get(key, "").strip()
        if value.lower().startswith(("replace", "change-me", "changeme", "your_", "your-")):
            errors.append(key + " must be a real secret, not a template placeholder")
    if environ.get("POS_CORE_API_KEY") and len(environ["POS_CORE_API_KEY"]) < 32:
        errors.append("POS_CORE_API_KEY must contain at least 32 characters in production")
    for key in ["FRONTEND_URL", "POS_CORE_API_BASE_URL"]:
        if environ.get(key) and not environ[key].startswith("https://"):
            errors.append(key + " must use HTTPS in production")
        if urlparse(environ.get(key, "")).hostname in {"localhost", "127.0.0.1", "::1"}:
            errors.append(key + " must not point to localhost in production")
    if environ.get("POS_CORE_API_KEY") == "dev-admincore-pos-bridge-key":
        errors.append("Replace the development POS bridge key in both production services")
    regex = environ.get("CORS_ORIGIN_REGEX", "").strip()
    if regex and (regex in {".*", ".+", "https://.*", "https?://.*"} or re.search(r"vercel|onrender|netlify", regex)):
        errors.append("CORS_ORIGIN_REGEX trusts every site on a shared hosting domain; list your exact frontend origins instead")
    if errors and raise_on_error:
        raise RuntimeError("; ".join(errors))
    return {"production": True, "ok": not errors, "errors": errors}
