"""Minimal doctor authentication.

Scope note: this is demo-grade, and deliberately so — the spec lists
production authentication as future scope. What it does provide is the
property that actually matters for the MVP: doctor endpoints are not open to
anyone who knows the URL, and every decision carries an identity that lands in
the audit log.

Real deployment needs an identity provider, hashed credentials in a user store,
and signed tokens. Do not ship this as-is.
"""

from __future__ import annotations

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
#: function, and the only account that exists is "doctor", so throttling that
#: name already throttles the attack. Values are epoch timestamps of failed
#: attempts within the current window; a successful login clears the entry.
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_SECONDS = 15 * 60
_FAILED_ATTEMPTS: Dict[str, List[float]] = {}


class AccountLockedError(Exception):
    """Raised instead of a plain auth failure when the lockout window is
    active, so the router can tell the caller "too many attempts, try again
    in N minutes" instead of an indistinguishable "wrong password" — the
    ambiguity is a real usability problem on a single-account demo (a doctor
    who mistypes their own password 5 times gets locked out and then can't
    tell that from having the wrong password), and hiding lockout state has
    little security value here since there is only one account to probe."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Locked out for {retry_after_seconds}s")


def _credentials() -> tuple[str, str]:
    """Credentials come from env so they are not committed to the repo."""
    return (
        os.getenv("DOCTOR_USERNAME", "doctor"),
        os.getenv("DOCTOR_PASSWORD", "doctorpassword123"),
    )


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
    lockout_key = (username or "").strip().lower() or "unknown"
    retry_after = _lockout_remaining_seconds(lockout_key)
    if retry_after > 0:
        raise AccountLockedError(retry_after)

    expected_user, expected_password = _credentials()

    # compare_digest on both fields to avoid leaking validity through timing.
    user_ok = hmac.compare_digest(username or "", expected_user)
    password_ok = hmac.compare_digest(password or "", expected_password)
    if not (user_ok and password_ok):
        _record_failure(lockout_key)
        return None

    _FAILED_ATTEMPTS.pop(lockout_key, None)
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=_TOKEN_TTL_HOURS)).isoformat()
    record = {
        "doctor_id": os.getenv("DOCTOR_ID", "DR-101"),
        "doctor_name": os.getenv("DOCTOR_NAME", "Dr. Sharma, MD"),
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
