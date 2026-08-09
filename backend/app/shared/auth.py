"""Doctor authentication and account registry.

Scope note: this is demo-grade, and deliberately so — the spec lists
production authentication as future scope. What it does provide is the
property that actually matters for the MVP: doctor endpoints are not open to
anyone who knows the URL, every decision carries an identity that lands in
the audit log, and passwords are never stored or compared in plain text.

Real deployment needs a real identity provider (OAuth/SSO) in front of this.
Do not ship this as-is.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Header, HTTPException, status

_TOKEN_TTL_HOURS = 12

#: token -> {doctor_id, doctor_name, expires_at}
_ACTIVE_TOKENS: Dict[str, Dict[str, str]] = {}

#: Brute-force protection on login. Keyed by the attempted username rather
#: than an IP address — the router has no request context to hand this
#: function. Values are epoch timestamps of failed attempts within the
#: current window; a successful login clears the entry. Per-username, so one
#: doctor's lockout never blocks another's account.
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_SECONDS = 15 * 60
_FAILED_ATTEMPTS: Dict[str, List[float]] = {}

#: PBKDF2-HMAC-SHA256 iteration count. OWASP's current minimum guidance for
#: this algorithm; stdlib-only (hashlib) rather than adding a bcrypt/argon2
#: dependency the whole team would need to reinstall mid-hackathon — the
#: same hand-roll-over-new-dependency choice this codebase already makes
#: elsewhere (the TF-IDF RAG engine has no vector-DB dependency either).
_PBKDF2_ITERATIONS = 100_000


class AccountLockedError(Exception):
    """Raised instead of a plain auth failure when the lockout window is
    active, so the router can tell the caller "too many attempts, try again
    in N minutes" instead of an indistinguishable "wrong password" — a
    doctor who mistypes their own password a few times can otherwise not
    tell that from having genuinely forgotten it."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Locked out for {retry_after_seconds}s")


class DuplicateUsernameError(Exception):
    """Raised by register_doctor when the username is already taken."""


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Returns (password_hash, password_salt), both hex strings. A fresh
    random salt is generated when one isn't supplied (registration); an
    existing salt is passed back in only by verify_password."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    candidate_hash, _ = hash_password(password, salt=password_salt)
    return hmac.compare_digest(candidate_hash, password_hash)


def register_doctor(
    username: str, password: str, name: str, qualification: str = "", registration_no: str = "",
):
    """Create a new doctor account. Raises DuplicateUsernameError if taken —
    the router turns that into a 409, same pattern as every other conflict
    response in this codebase."""
    from app.shared.database import db  # local import avoids a load-time cycle
    from app.shared.models import Doctor

    if db.get_doctor_by_username(username):
        raise DuplicateUsernameError(username)

    password_hash, password_salt = hash_password(password)
    doctor = Doctor(
        username=username, password_hash=password_hash, password_salt=password_salt,
        name=name, qualification=qualification, registration_no=registration_no,
    )
    return db.save_doctor(doctor)


def _lockout_remaining_seconds(key: str) -> int:
    now = time.time()
    attempts = [t for t in _FAILED_ATTEMPTS.get(key, []) if now - t < _LOCKOUT_SECONDS]
    _FAILED_ATTEMPTS[key] = attempts
    if len(attempts) < _MAX_FAILED_ATTEMPTS:
        return 0
    return max(1, int(_LOCKOUT_SECONDS - (now - min(attempts))))


def _record_failure(key: str) -> None:
    _FAILED_ATTEMPTS.setdefault(key, []).append(time.time())


def authenticate(username: str, password: str) -> Optional[Dict[str, str]]:
    from app.shared.database import db  # local import avoids a load-time cycle

    lockout_key = (username or "").strip().lower() or "unknown"
    retry_after = _lockout_remaining_seconds(lockout_key)
    if retry_after > 0:
        raise AccountLockedError(retry_after)

    doctor = db.get_doctor_by_username(username or "")
    # Always run verify_password, even with no matching account — a real
    # digest computation either way keeps a nonexistent-username response
    # taking the same time as a wrong-password one, not a comparison the
    # timing side-channel could otherwise distinguish.
    password_ok = verify_password(
        password or "",
        doctor.password_hash if doctor else hash_password("")[0],
        doctor.password_salt if doctor else secrets.token_hex(16),
    )
    if not doctor or not password_ok:
        _record_failure(lockout_key)
        return None

    _FAILED_ATTEMPTS.pop(lockout_key, None)
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=_TOKEN_TTL_HOURS)).isoformat()
    record = {
        "doctor_id": doctor.doctor_id,
        "doctor_name": doctor.name,
        "expires_at": expires_at,
        "token": token,
    }
    _ACTIVE_TOKENS[token] = record
    return record


def resolve_token(token: str) -> Optional[Dict[str, str]]:
    record = _ACTIVE_TOKENS.get(token)
    if not record:
        return None
    if datetime.fromisoformat(record["expires_at"]) < datetime.now(timezone.utc):
        _ACTIVE_TOKENS.pop(token, None)
        return None
    return record


async def require_doctor(authorization: Optional[str] = Header(default=None)) -> Dict[str, str]:
    """FastAPI dependency guarding every doctor endpoint."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Doctor authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    record = resolve_token(authorization.split(" ", 1)[1].strip())
    if not record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return record
