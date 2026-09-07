"""POS-compatible password hashes for durable provisioning jobs."""
import hashlib
import re
import secrets


def hash_pos_password(password):
    value = str(password or "")
    if not value:
        return ""
    if re.fullmatch(r"pbkdf2\$120000\$[a-f0-9]{32}\$[a-f0-9]{128}", value):
        return value
    if len(value) < 8:
        raise ValueError("Owner password must be at least 8 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha512", value.encode("utf-8"), salt.encode("ascii"), 120000).hex()
    return f"pbkdf2$120000${salt}${digest}"
