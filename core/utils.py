from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone


OTP_SESSION_KEY = "otp"
OTP_EXPIRY_SECONDS = 120
OTP_RESEND_COOLDOWN_SECONDS = 30


def _hash_otp(otp: str) -> str:
    payload = f"{settings.SECRET_KEY}|{otp}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def generate_otp() -> str:
    return "123456"

def create_otp_session(*, request, user_id: int, remember_me: bool) -> str:
    otp = generate_otp()
    now = int(time.time())
    request.session[OTP_SESSION_KEY] = {
        "user_id": user_id,
        "otp_hash": _hash_otp(otp),
        "expires_at": now + OTP_EXPIRY_SECONDS,
        "last_sent_at": now,
        "remember_me": bool(remember_me),
    }
    request.session.modified = True
    return otp


@dataclass(frozen=True)
class OtpCheckResult:
    ok: bool
    error: str | None = None
    user_id: int | None = None
    remember_me: bool = False


def verify_otp_session(*, request, otp_code: str) -> OtpCheckResult:
    blob = request.session.get(OTP_SESSION_KEY)
    if not blob:
        return OtpCheckResult(ok=False, error="No OTP session found. Please login again.")

    now = int(time.time())
    if now > int(blob.get("expires_at", 0)):
        return OtpCheckResult(ok=False, error="OTP expired. Please login again.")

    otp_code = (otp_code or "").strip()
    if len(otp_code) != 6 or not otp_code.isdigit():
        return OtpCheckResult(ok=False, error="Enter the 6-digit OTP.")

    if _hash_otp(otp_code) != blob.get("otp_hash"):
        return OtpCheckResult(ok=False, error="Incorrect OTP. Please try again.")

    return OtpCheckResult(
        ok=True,
        user_id=int(blob["user_id"]),
        remember_me=bool(blob.get("remember_me", False)),
    )


def clear_otp_session(*, request) -> None:
    if OTP_SESSION_KEY in request.session:
        del request.session[OTP_SESSION_KEY]
        request.session.modified = True


def can_resend_otp(*, request) -> tuple[bool, int]:
    blob = request.session.get(OTP_SESSION_KEY) or {}
    last = int(blob.get("last_sent_at", 0))
    now = int(time.time())
    remaining = OTP_RESEND_COOLDOWN_SECONDS - (now - last)
    return (remaining <= 0, max(0, remaining))


def resend_otp_session(*, request) -> str | None:
    blob = request.session.get(OTP_SESSION_KEY)
    if not blob:
        return None
    otp = generate_otp()
    now = int(time.time())
    blob["otp_hash"] = _hash_otp(otp)
    blob["expires_at"] = now + OTP_EXPIRY_SECONDS
    blob["last_sent_at"] = now
    request.session[OTP_SESSION_KEY] = blob
    request.session.modified = True
    return otp


def role_home_url(role: str) -> str:
    role = (role or "").lower()
    if role == "admin":
        return "/dashboard/"
    if role == "manager":
        return "/dashboard/"
    if role in ("picker", "packer", "putaway"):
        return "/dashboard/"
    return "/dashboard/"


def now_date_str() -> str:
    return timezone.localdate().strftime("%A, %d %B %Y")
