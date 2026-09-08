"""Validate deployment-critical settings before accepting requests."""


def validate_production_config(environ):
    production = any(environ.get(key, "").lower() == "production" for key in ["ENVIRONMENT", "APP_ENV", "NODE_ENV"])
    production = production or environ.get("RENDER", "").lower() == "true"
    if not production:
        return
    missing = [key for key in ["MONGO_URL", "DB_NAME", "JWT_SECRET", "FRONTEND_URL", "POS_CORE_API_BASE_URL", "POS_CORE_API_KEY"] if not environ.get(key, "").strip()]
    if missing:
        raise RuntimeError("Missing production settings: " + ", ".join(missing))
    if len(environ["JWT_SECRET"]) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters in production")
    for key in ["FRONTEND_URL", "POS_CORE_API_BASE_URL"]:
        if not environ[key].startswith("https://"):
            raise RuntimeError(key + " must use HTTPS in production")
    if environ["POS_CORE_API_KEY"] == "dev-admincore-pos-bridge-key":
        raise RuntimeError("Replace the development POS bridge key in both production services")
