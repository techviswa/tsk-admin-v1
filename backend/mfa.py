"""TOTP (RFC 6238) multi-factor authentication primitives for AdminCore.

Only standard-library crypto is used for the one-time codes. Secrets are encrypted at rest with Fernet
(cryptography, already a declared dependency). MFA state lives in its own collection (`user_mfa`) so it can
never leak through a user-document projection.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

STEP_SECONDS = 30
DIGITS = 6
ISSUER = "Taskoora Admin"
RECOVERY_CODE_COUNT = 8


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return f"{value:0{DIGITS}d}"


def normalize_code(code: Optional[str]) -> str:
    return re.sub(r"[\s-]", "", code or "")


def is_totp_shaped(code: Optional[str]) -> bool:
    normalized = normalize_code(code)
    return len(normalized) == DIGITS and normalized.isdigit()


def verify_totp(secret_b32: str, code: Optional[str], now: Optional[float] = None,
                window: int = 1, last_step: Optional[int] = None) -> Optional[int]:
    """Return the matched time step, or None. A step at or before `last_step` is a replay and is refused."""
    if not is_totp_shaped(code):
        return None
    supplied = normalize_code(code)
    current_step = int((now if now is not None else time.time()) // STEP_SECONDS)
    matched = None
    for offset in range(-window, window + 1):
        step = current_step + offset
        # Evaluate every candidate so timing does not reveal which step matched.
        if hmac.compare_digest(_hotp(secret_b32, step), supplied) and (last_step is None or step > last_step):
            matched = step if matched is None else max(matched, step)
    return matched


def provisioning_uri(secret_b32: str, account: str, issuer: str = ISSUER) -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


def _fernet() -> Fernet:
    material = os.environ.get("MFA_ENCRYPTION_KEY") or os.environ.get("JWT_SECRET") or "dev-only-mfa-key"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest()))


def encrypt_secret(secret_b32: str) -> str:
    return _fernet().encrypt(secret_b32.encode("ascii")).decode("ascii")


def decrypt_secret(token: str) -> Optional[str]:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError):
        return None


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(normalize_code(code).lower().encode("utf-8")).hexdigest()


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT):
    """Returns (plaintext codes to show once, hashes to store)."""
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(count)]
    return codes, [_hash_recovery(code) for code in codes]


def consume_recovery_code(stored_hashes, code: Optional[str]):
    """Return the remaining hashes if `code` matched one (single use), else None."""
    supplied = _hash_recovery(code or "")
    remaining, matched = [], False
    for stored in stored_hashes or []:
        if not matched and hmac.compare_digest(stored, supplied):
            matched = True
            continue
        remaining.append(stored)
    return remaining if matched else None
