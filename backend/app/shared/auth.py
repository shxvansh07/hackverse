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
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import Header, HTTPException, status

_TOKEN_TTL_HOURS = 12

#: token -> {doctor_id, doctor_name, expires_at}
_ACTIVE_TOKENS: Dict[str, Dict[str, str]] = {}


def _credentials() -> tuple[str, str]:
    """Credentials come from env so they are not committed to the repo."""
    return (
        os.getenv("DOCTOR_USERNAME", "doctor"),
        os.getenv("DOCTOR_PASSWORD", "doctorpassword123"),
    )


def authenticate(username: str, password: str) -> Optional[Dict[str, str]]:
    expected_user, expected_password = _credentials()

    # compare_digest on both fields to avoid leaking validity through timing.
    user_ok = hmac.compare_digest(username or "", expected_user)
    password_ok = hmac.compare_digest(password or "", expected_password)
    if not (user_ok and password_ok):
        return None

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
