import os
import sys
import uuid
import html
import hashlib
import math
import re
import secrets
import smtplib
import ssl
import time
import unicodedata
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from typing import Optional
import bcrypt
import jwt
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config_loader import cfg

# JWT Configuration
JWT_SECRET = cfg.jwt_secret
JWT_ALGORITHM = cfg.jwt_algorithm
JWT_EXPIRATION_HOURS = cfg.jwt_expiration_hours

# Connect to MongoDB
mongo_client = MongoClient(cfg.mongo_uri)
db = mongo_client[cfg.mongo_database]
users_col = db["users"]
pending_otps_col = db["pending_otps"]

# SMTP Configuration
SMTP_HOST = cfg.smtp_host
SMTP_PORT = cfg.smtp_port
SMTP_USERNAME = cfg.smtp_username
SMTP_APP_PASSWORD = cfg.smtp_app_password
SMTP_FROM_NAME = cfg.smtp_from_name
PUBLIC_SITE_URL = cfg.public_site_url
OTP_TTL_MINUTES = 10
OTP_RESEND_COOLDOWN_SECONDS = 60
_pending_otp_indexes_ready = False
_user_indexes_ready = False

auth_router = APIRouter()

_ALLOWED_AVATAR_HOSTS = {"lh3.googleusercontent.com", "googleusercontent.com"}


@auth_router.get("/api/avatar/google")
async def google_avatar_proxy(url: str):
    parsed = urlparse(unquote(url))
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not any(host == h or host.endswith(f".{h}") for h in _ALLOWED_AVATAR_HOSTS):
        raise HTTPException(status_code=400, detail="Unsupported avatar URL")
    try:
        req = urllib.request.Request(
            parsed.geturl(),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            content = response.read(2 * 1024 * 1024)
            content_type = response.headers.get("Content-Type", "image/jpeg")
    except Exception:
        raise HTTPException(status_code=502, detail="Unable to load avatar")
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Avatar URL did not return an image")
    return Response(content=content, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})

# ─── Pydantic Models ───────────────────────────────────
class AuthRequest(BaseModel):
    username: str
    password: str
    language: Optional[str] = "en"
    phone: Optional[str] = None
    display_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_country_code: Optional[str] = None
    country_code: Optional[str] = None
    country_iso: Optional[str] = None
    phone_number: Optional[str] = None
    region: Optional[str] = None

class VerifyOTPRequest(BaseModel):
    user_id: str
    otp: str

class ResendOTPRequest(BaseModel):
    user_id: str

class PasswordResetRequest(BaseModel):
    email: str
    language: Optional[str] = "en"

class VerifyPasswordResetOTPRequest(BaseModel):
    reset_id: str
    otp: str

class ResendPasswordResetOTPRequest(BaseModel):
    reset_id: str

class CompletePasswordResetRequest(BaseModel):
    reset_id: str
    reset_token: str
    new_password: str

class PhoneValidationRequest(BaseModel):
    country_iso: str
    phone_number: str

class OAuthRequest(BaseModel):
    token: str
    language: Optional[str] = "en"
    phone: Optional[str] = None

class PreferencesRequest(BaseModel):
    language: Optional[str] = None

class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class CompleteGoogleProfileRequest(BaseModel):
    display_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    phone_country_code: Optional[str] = None
    country_code: Optional[str] = None
    country_iso: Optional[str] = None
    phone_number: Optional[str] = None
    region: Optional[str] = None

class UpdateEmailRequest(BaseModel):
    new_email: str

class SendEmailOTPRequest(BaseModel):
    new_email: str

class UpdateEmailWithOTPRequest(BaseModel):
    pending_id: str
    otp: str

class SetPasswordRequest(BaseModel):
    new_password: str

class LinkGoogleRequest(BaseModel):
    token: str

# ─── Utility Functions ─────────────────────────────────
def _bcrypt_prehash(password: str) -> bytes:
    # Pre-hash with SHA-256 so arbitrarily long passwords become safe for bcrypt.
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("utf-8")


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_prehash(password), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    if hashed_password.startswith("$2"):
        hashed_bytes = hashed_password.encode("utf-8")
        # New scheme: bcrypt(sha256(password))
        try:
            if bcrypt.checkpw(_bcrypt_prehash(plain_password), hashed_bytes):
                return True
        except ValueError:
            return False

        # Legacy scheme: bcrypt(password) for older short-password accounts.
        plain_bytes = plain_password.encode("utf-8")
        if len(plain_bytes) <= 72:
            try:
                if bcrypt.checkpw(plain_bytes, hashed_bytes):
                    return True
            except ValueError:
                return False

    # Fallback for old sha256 testing accounts
    legacy_hash = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
    return legacy_hash == hashed_password


def maybe_upgrade_password_hash(plain_password: str, hashed_password: str) -> str | None:
    if not hashed_password:
        return None
    if not hashed_password.startswith("$2"):
        if hashlib.sha256(plain_password.encode("utf-8")).hexdigest() == hashed_password:
            return get_password_hash(plain_password)
        return None

    hashed_bytes = hashed_password.encode("utf-8")
    try:
        if bcrypt.checkpw(_bcrypt_prehash(plain_password), hashed_bytes):
            return None
    except ValueError:
        return None

    plain_bytes = plain_password.encode("utf-8")
    if len(plain_bytes) <= 72:
        try:
            if bcrypt.checkpw(plain_bytes, hashed_bytes):
                return get_password_hash(plain_password)
        except ValueError:
            return None
    return None


def _validate_new_password(password: str) -> None:
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must contain at least 8 characters")
    if len(password) > 200:
        raise HTTPException(status_code=400, detail="Password too long")
    if not re.match(r"[A-Z]", password):
        raise HTTPException(status_code=400, detail="Password must start with an uppercase letter")
    if not re.search(r"[^\w\s]", password):
        raise HTTPException(status_code=400, detail="Password must contain at least one special character")

def create_jwt_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    to_encode.update({"iat": int(time.time()), "exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _set_session_cookie(response: Response, token: str) -> None:
    """Provide an HttpOnly session for protected file URLs and browser navigation."""
    response.set_cookie(
        key="msme_session",
        value=token,
        max_age=JWT_EXPIRATION_HOURS * 60 * 60,
        httponly=True,
        secure=PUBLIC_SITE_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )

def generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"

def generate_display_name(username: str = "") -> str:
    suffix = secrets.token_hex(2).upper()
    return f"bisnes.ai Member {suffix}"

def _user_display_name(user: dict | None) -> str:
    if not user:
        return ""
    first_name = (user.get("first_name") or "").strip()
    last_name = (user.get("last_name") or "").strip()
    if first_name and last_name:
        return f"{first_name} {last_name}"
    display_name = (user.get("display_name") or user.get("name") or "").strip()
    if display_name:
        return display_name
    username = (user.get("username") or "").strip()
    if "@" in username:
        return username.split("@")[0]
    return username or generate_display_name()

def _normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()

_NAME_PART_MAX_LENGTH = 64
_NAME_ALLOWED_PUNCTUATION = {" ", "-", "'", "’", "."}

def _normalize_name_part(value: Optional[str], field_label: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_label} is required")
    if len(normalized) > _NAME_PART_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=f"{field_label} is too long (max {_NAME_PART_MAX_LENGTH} characters)")
    if not any(character.isalpha() for character in normalized):
        raise HTTPException(status_code=400, detail=f"{field_label} must contain at least one letter")
    if any(not (character.isalpha() or character in _NAME_ALLOWED_PUNCTUATION) for character in normalized):
        raise HTTPException(
            status_code=400,
            detail=f"{field_label} can only contain letters, spaces, hyphens, apostrophes, and periods",
        )
    return normalized

def _split_legacy_display_name(value: Optional[str]) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()
    if not normalized:
        return "", ""
    parts = normalized.split(" ", 1)
    return parts[0], parts[1] if len(parts) == 2 else ""

def _resolve_identity(
    first_name: Optional[str],
    last_name: Optional[str],
    display_name: Optional[str] = None,
) -> tuple[str, str, str]:
    # display_name is retained only as a backward-compatible source for older clients.
    if first_name is None and last_name is None and display_name is not None:
        first_name, last_name = _split_legacy_display_name(display_name)
    first = _normalize_name_part(first_name, "First name")
    last = _normalize_name_part(last_name, "Last name")
    return first, last, f"{first} {last}"

def _identity_from_google_profile(user_info: dict, email: str) -> tuple[str, str, str, bool]:
    raw_name = user_info.get("name") or (email.split("@", 1)[0] if email else "")
    first_candidate = user_info.get("given_name")
    last_candidate = user_info.get("family_name")
    if not first_candidate or not last_candidate:
        fallback_first, fallback_last = _split_legacy_display_name(raw_name)
        first_candidate = first_candidate or fallback_first
        last_candidate = last_candidate or fallback_last
    if first_candidate and last_candidate:
        try:
            first, last, display = _resolve_identity(first_candidate, last_candidate)
            return first, last, display, True
        except HTTPException:
            pass
    # Google profiles can legitimately omit a family name. Preserve the source
    # display name and require the user to complete the two new fields later.
    return "", "", re.sub(r"\s+", " ", raw_name).strip() or generate_display_name(email), False

def _active_user_by_email(email: Optional[str]) -> dict | None:
    normalized = _normalize_email(email)
    if not normalized:
        return None
    return users_col.find_one({"username": normalized, "status": "active"})

def _ensure_google_email_can_attach(user: dict, google_email: str) -> str:
    normalized_google_email = _normalize_email(google_email)
    account_email = _normalize_email(user.get("username"))
    if not normalized_google_email or "@" not in normalized_google_email:
        raise HTTPException(status_code=400, detail="A valid Google email is required")
    if not account_email or "@" not in account_email:
        raise HTTPException(status_code=409, detail="Please add an email address to this account before linking Google")
    if account_email != normalized_google_email:
        raise HTTPException(
            status_code=409,
            detail="Google email must match your bisnes.ai account email to link sign-in"
        )
    owner = _active_user_by_email(normalized_google_email)
    if owner and str(owner.get("_id")) != str(user.get("_id")):
        raise HTTPException(status_code=409, detail="This Google email is already used by another account")
    return normalized_google_email

def _user_preferences(user: dict | None) -> dict:
    prefs = (user or {}).get("preferences") or {}
    return {
        "language": _normalize_language(prefs.get("language") or (user or {}).get("language") or "en"),
    }

def _validate_phone_number(country_iso: Optional[str], phone_number: Optional[str]) -> dict:
    iso = (country_iso or "").strip().upper()
    raw_number = (phone_number or "").strip()
    if not iso or not re.fullmatch(r"[A-Z]{2}", iso):
        raise HTTPException(status_code=400, detail="Please select a valid country or region")
    if not raw_number:
        raise HTTPException(status_code=400, detail="Phone number is required")
    if not re.fullmatch(r"[0-9]+", raw_number):
        raise HTTPException(status_code=400, detail="Phone number can only contain digits")
    try:
        parsed = phonenumbers.parse(raw_number, iso)
    except NumberParseException:
        raise HTTPException(status_code=400, detail="Enter a valid phone number for the selected country or region")
    if not phonenumbers.is_valid_number_for_region(parsed, iso):
        raise HTTPException(status_code=400, detail="Enter a valid phone number for the selected country or region")
    return {
        "country_code": f"+{parsed.country_code}",
        "country_iso": iso,
        "phone_number": raw_number,
        "phone_e164": phonenumbers.format_number(parsed, PhoneNumberFormat.E164),
    }

def _legacy_phone_value(phone_e164: Optional[str]) -> str:
    # Keep the old field during the migration window for legacy readers.
    return (phone_e164 or "").strip()

def _clean_region(value: Optional[str]) -> str:
    region = re.sub(r"\s+", " ", (value or "").strip())
    if not region:
        raise HTTPException(status_code=400, detail="Region is required")
    if len(region) > 80:
        raise HTTPException(status_code=400, detail="Region is too long")
    return region

def _requires_profile_completion(user: dict | None) -> bool:
    if not user:
        return False
    if "first_name" in user or "last_name" in user:
        return not bool(
            (user.get("first_name") or "").strip()
            and (user.get("last_name") or "").strip()
            and (user.get("phone_e164") or user.get("phone") or "").strip()
            and (user.get("country_iso") or "").strip()
        )
    return not bool((user.get("display_name") or user.get("name") or "").strip())

def _profile_completion_page(user: dict | None) -> Optional[str]:
    if not _requires_profile_completion(user):
        return None
    return "register"

def _ensure_user_indexes() -> None:
    global _user_indexes_ready
    if _user_indexes_ready:
        return
    users_col.create_index("username", unique=True, sparse=True)
    users_col.create_index("phone", unique=True, sparse=True)
    # phone_e164 gets its unique index from the identity migration script after
    # legacy duplicates have been reviewed. The legacy phone index remains the
    # concurrency guard while both fields are written.
    users_col.create_index("auth_provider_id", sparse=True)
    _user_indexes_ready = True

def _auth_user(request: Request) -> dict:
    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    candidates = []
    if scheme.lower() == "bearer" and token:
        candidates.append(token)
    cookie_token = request.cookies.get("msme_session", "")
    if cookie_token and cookie_token not in candidates:
        candidates.append(cookie_token)
    if not candidates:
        raise HTTPException(status_code=401, detail="Missing authorization token")
    expired = False
    for candidate in candidates:
        try:
            payload = jwt.decode(candidate, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            expired = True
            continue
        except jwt.InvalidTokenError:
            continue
        user_id = payload.get("sub")
        if not user_id:
            continue
        user = users_col.find_one({"_id": str(user_id)})
        if user:
            # Password-reset completion advances this marker. Tokens created
            # before it (including legacy tokens without iat) cannot keep an
            # old browser session alive after the password has changed.
            password_changed_at_epoch = user.get("password_changed_at_epoch")
            token_issued_at = payload.get("iat", 0)
            if password_changed_at_epoch is not None:
                try:
                    if int(token_issued_at) < int(password_changed_at_epoch):
                        continue
                except (TypeError, ValueError):
                    continue
            return user
    if expired:
        raise HTTPException(status_code=401, detail="Session expired")
    raise HTTPException(status_code=401, detail="Invalid authorization token")

def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "Unknown"

def _device_info(request: Request) -> str:
    ua = request.headers.get("user-agent", "").strip()
    return ua[:180] if ua else "Unknown device"

def _current_time_for_email() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")

OTP_EMAIL_COPY = {
    "zh": {
        "subject": "您的 bisnes.ai 验证码",
        "title": "您的 bisnes.ai 验证码",
        "intro": "请使用以下验证码继续登录或注册 bisnes.ai。",
        "validity": "此验证码将在 10 分钟后失效。若非您本人请求，可直接忽略此邮件。",
        "footer": "© bisnes.ai",
        "plain_code": "验证码",
    },
    "en": {
        "subject": "Your bisnes.ai verification code",
        "title": "Your bisnes.ai verification code",
        "intro": "Use the verification code below to continue signing in to bisnes.ai.",
        "validity": "This code will expire in 10 minutes. If you didn’t request this code, you can safely ignore this email.",
        "footer": "© bisnes.ai",
        "plain_code": "Verification code",
    },
    "ms": {
        "subject": "Kod pengesahan bisnes.ai anda",
        "title": "Kod pengesahan bisnes.ai anda",
        "intro": "Gunakan kod pengesahan di bawah untuk meneruskan log masuk atau pendaftaran ke bisnes.ai.",
        "validity": "Kod ini akan tamat tempoh dalam 10 minit. Jika anda tidak meminta kod ini, anda boleh mengabaikan e-mel ini dengan selamat.",
        "footer": "© bisnes.ai",
        "plain_code": "Kod pengesahan",
    },
}

PASSWORD_RESET_EMAIL_COPY = {
    "zh": {
        "subject": "重置您的 bisnes.ai 密码",
        "title": "重置您的 bisnes.ai 密码",
        "intro": "请使用以下验证码继续重置您的 bisnes.ai 密码。",
        "validity": "此验证码将在 10 分钟后失效。若非您本人请求重置密码，可直接忽略此邮件。",
        "footer": "© bisnes.ai",
        "plain_code": "验证码",
    },
    "en": {
        "subject": "Reset your bisnes.ai password",
        "title": "Your bisnes.ai password reset code",
        "intro": "Use the code below to continue resetting your bisnes.ai password.",
        "validity": "This code will expire in 10 minutes. If you didn’t request a password reset, you can safely ignore this email.",
        "footer": "© bisnes.ai",
        "plain_code": "Reset code",
    },
    "ms": {
        "subject": "Tetapkan semula kata laluan bisnes.ai anda",
        "title": "Kod tetapan semula kata laluan bisnes.ai anda",
        "intro": "Gunakan kod di bawah untuk meneruskan tetapan semula kata laluan bisnes.ai anda.",
        "validity": "Kod ini akan tamat tempoh dalam 10 minit. Jika anda tidak meminta tetapan semula kata laluan, anda boleh mengabaikan e-mel ini dengan selamat.",
        "footer": "© bisnes.ai",
        "plain_code": "Kod tetapan semula",
    },
}

def _normalize_language(language: Optional[str]) -> str:
    lang = (language or "en").strip().lower()
    return lang if lang in OTP_EMAIL_COPY else "en"


def _otp_email_copy(language: Optional[str], purpose: str = "verification") -> dict:
    lang = _normalize_language(language)
    return (PASSWORD_RESET_EMAIL_COPY if purpose == "password_reset" else OTP_EMAIL_COPY)[lang]

def build_otp_email_html(
    otp_code: str,
    current_time: str,
    device_info: str,
    ip_address: str,
    language: Optional[str],
    purpose: str = "verification",
) -> str:
    lang = _normalize_language(language)
    copy = _otp_email_copy(lang, purpose)
    safe_otp = html.escape(otp_code)
    safe_intro = html.escape(copy["intro"])
    safe_validity = html.escape(copy["validity"])
    safe_title = html.escape(copy["title"])
    safe_footer = html.escape(copy["footer"])
    safe_email_lang = "zh-CN" if lang == "zh" else ("ms" if lang == "ms" else "en")

    return f"""<!doctype html>
<html lang="{safe_email_lang}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title}</title>
</head>
<body style="margin:0;padding:0;background:#f7f7f5;font-family:Arial,'Helvetica Neue',Helvetica,sans-serif;color:#1a1a1a;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f7f7f5;margin:0;padding:40px 12px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;max-width:560px;background:#ffffff;border:1px solid #e7e7e4;border-radius:12px;border-collapse:separate;">
                    <tr>
                        <td style="padding:32px 36px 0;font-size:16px;line-height:20px;font-weight:700;letter-spacing:-0.2px;color:#171717;">
                            bisnes.ai
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:32px 36px 0;">
                            <h1 style="margin:0;font-size:24px;line-height:32px;font-weight:600;letter-spacing:-0.35px;color:#171717;">{safe_title}</h1>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:16px 36px 0;">
                            <p style="margin:0;font-size:16px;line-height:24px;color:#4d4d4a;">{safe_intro}</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:28px 36px 0;">
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:separate;background:#f4f4f1;border:1px solid #e7e7e4;border-radius:10px;">
                                <tr>
                                    <td align="center" style="padding:20px 16px;font-size:32px;line-height:36px;font-weight:600;letter-spacing:8px;color:#171717;font-variant-numeric:tabular-nums;">{safe_otp}</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:24px 36px 36px;">
                            <p style="margin:0;font-size:14px;line-height:21px;color:#6a6a66;">{safe_validity}</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="border-top:1px solid #ececea;padding:22px 36px 26px;">
                            <p style="margin:0;font-size:12px;line-height:18px;color:#8a8a86;">{safe_footer}</p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

def send_otp_email(
    to_email: str,
    otp_code: str,
    current_time: str,
    device_info: str,
    ip_address: str,
    language: Optional[str] = "en",
    purpose: str = "verification",
) -> None:
    if not SMTP_APP_PASSWORD:
        raise RuntimeError("SMTP_APP_PASSWORD is not configured")

    lang = _normalize_language(language)
    copy = _otp_email_copy(lang, purpose)
    msg = MIMEMultipart("related")
    msg["Subject"] = copy["subject"]
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_USERNAME))
    msg["To"] = to_email

    alternative = MIMEMultipart("alternative")
    text_body = (
        f"{copy['title']}\n\n"
        f"{copy['intro']}\n\n"
        f"{copy['plain_code']}: {otp_code}\n\n"
        f"{copy['validity']}\n\n"
        f"{copy['footer']}"
    )
    alternative.attach(MIMEText(text_body, "plain", "utf-8"))
    alternative.attach(MIMEText(
        build_otp_email_html(otp_code, current_time, device_info, ip_address, lang, purpose),
        "html",
        "utf-8",
    ))
    msg.attach(alternative)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(SMTP_USERNAME, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_USERNAME, [to_email], msg.as_string())

def _raise_otp_email_delivery_error(exc: Exception) -> None:
    print(f"[OTP_EMAIL_ERROR] {type(exc).__name__}: {exc}", flush=True)
    raise HTTPException(
        status_code=502,
        detail="We couldn't send the OTP email right now. Please try again later or contact support."
    )

def _store_pending_otp(
    pending_id: str,
    username: str,
    phone_e164: str,
    password_hash: str,
    otp_code: str,
    current_time: str,
    device_info: str,
    ip_address: str,
    language: Optional[str],
    first_name: str,
    last_name: str,
    country_code: str,
    country_iso: str,
    phone_number: str,
    region: Optional[str] = None,
) -> None:
    _ensure_pending_otp_indexes()
    now = datetime.utcnow()
    pending_otps_col.delete_many({"username": username})
    pending_otps_col.insert_one({
        "_id": pending_id,
        "username": username,
        "phone": _legacy_phone_value(phone_e164),
        "phone_e164": phone_e164,
        "phone_number": phone_number,
        "country_code": country_code,
        "country_iso": country_iso,
        "phone_verified_at": None,
        "first_name": first_name,
        "last_name": last_name,
        "display_name": f"{first_name} {last_name}",
        "region": region,
        "preferences": {
            "language": _normalize_language(language),
        },
        "password": password_hash,
        "otp": get_password_hash(otp_code),
        "created_at": now,
        "last_otp_sent_at": now,
        "resend_available_at": now + timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS),
        "expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
        "current_time": current_time,
        "device_info": device_info,
        "ip_address": ip_address,
        "language": _normalize_language(language),
    })

def _ensure_pending_otp_indexes() -> None:
    global _pending_otp_indexes_ready
    if _pending_otp_indexes_ready:
        return
    pending_otps_col.create_index("expires_at", expireAfterSeconds=0)
    pending_otps_col.create_index("username")
    # Password-reset codes are isolated from registration OTPs. One active
    # reset record per email makes request/resend rate limiting deterministic.
    pending_otps_col.create_index(
        [("type", 1), ("username", 1)],
        unique=True,
        partialFilterExpression={"type": "password_reset"},
        name="unique_password_reset_per_email",
    )
    _pending_otp_indexes_ready = True


def _otp_resend_retry_after_seconds(pending: dict, now: Optional[datetime] = None) -> int:
    """Return the remaining resend cooldown for a pending email OTP."""
    resend_available_at = pending.get("resend_available_at")
    if not isinstance(resend_available_at, datetime):
        return 0
    current_time = now or datetime.utcnow()
    seconds_remaining = (resend_available_at - current_time).total_seconds()
    return max(0, math.ceil(seconds_remaining))


def _otp_resend_rate_limited_error(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=f"Please wait {retry_after} seconds before requesting another code.",
        headers={"Retry-After": str(retry_after)},
    )

# ─── Auth Endpoints ────────────────────────────────────

@auth_router.post("/api/register")
async def register(req: AuthRequest, request: Request):
    _ensure_user_indexes()
    _ensure_pending_otp_indexes()
    username = req.username.strip().lower()
    first_name, last_name, display_name = _resolve_identity(req.first_name, req.last_name, req.display_name)
    phone_data = _validate_phone_number(req.country_iso, req.phone_number)
    region = _clean_region(req.region)
    language = _normalize_language(req.language)
    if not username or not req.password:
        raise HTTPException(status_code=400, detail="Username and password required")
    _validate_new_password(req.password)

    if "@" not in username:
        raise HTTPException(status_code=400, detail="Please use a valid email address for email registration")

    existing = users_col.find_one({"username": username})
    if existing and existing.get("status") == "active":
        if existing.get("auth_provider_id") and not existing.get("password"):
            raise HTTPException(status_code=400, detail="This email is already registered with Google. Please log in with Google.")
        raise HTTPException(status_code=400, detail="Username already taken")
    if users_col.find_one({"phone_e164": phone_data["phone_e164"], "status": "active"}) or users_col.find_one({"phone": phone_data["phone_e164"], "status": "active"}):
        raise HTTPException(status_code=400, detail="Phone number already registered")

    existing_pending = pending_otps_col.find_one({"username": username})
    if existing_pending:
        if datetime.utcnow() > existing_pending.get("expires_at", datetime.min):
            pending_otps_col.delete_one({"_id": existing_pending["_id"]})
        else:
            retry_after = _otp_resend_retry_after_seconds(existing_pending)
            if retry_after:
                raise _otp_resend_rate_limited_error(retry_after)
            # A legacy pending record without a cooldown may be safely replaced.
            pending_otps_col.delete_one({"_id": existing_pending["_id"]})

    pending_id = str(uuid.uuid4())
    otp_code = generate_otp()
    current_time = _current_time_for_email()
    device_info = _device_info(request)
    ip_address = _client_ip(request)

    _store_pending_otp(
        pending_id=pending_id,
        username=username,
        phone_e164=phone_data["phone_e164"],
        password_hash=get_password_hash(req.password),
        otp_code=otp_code,
        current_time=current_time,
        device_info=device_info,
        ip_address=ip_address,
        language=language,
        first_name=first_name,
        last_name=last_name,
        country_code=phone_data["country_code"],
        country_iso=phone_data["country_iso"],
        phone_number=phone_data["phone_number"],
        region=region,
    )

    try:
        send_otp_email(username, otp_code, current_time, device_info, ip_address, language)
    except Exception as exc:
        pending_otps_col.delete_one({"_id": pending_id})
        _raise_otp_email_delivery_error(exc)

    if existing and existing.get("status") != "active":
        users_col.delete_one({"_id": existing["_id"]})

    return {
        "status": "pending_verification",
        "username": username,
        "user_id": pending_id,
        "resend_available_in": OTP_RESEND_COOLDOWN_SECONDS,
    }

@auth_router.post("/api/verify-otp")
async def verify_otp(req: VerifyOTPRequest, response: Response):
    pending = pending_otps_col.find_one({"_id": req.user_id})
    if pending:
        if datetime.utcnow() > pending["expires_at"]:
            pending_otps_col.delete_one({"_id": req.user_id})
            raise HTTPException(status_code=400, detail="OTP has expired. Please register again to get a new code.")

        if not verify_password(req.otp, pending.get("otp", "")):
            raise HTTPException(status_code=400, detail="Invalid OTP code")

        username = pending["username"]
        return _finalize_registration_from_pending(pending, response=response)

    user = users_col.find_one({"_id": req.user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.get("status") == "active":
        raise HTTPException(status_code=400, detail="User already verified")

    if "otp_expiry" not in user or datetime.utcnow() > user["otp_expiry"]:
        raise HTTPException(status_code=400, detail="OTP has expired. Please register again to get a new code.")

    if not verify_password(req.otp, user.get("otp", "")):
        raise HTTPException(status_code=400, detail="Invalid OTP code")

    # Mark as active, wipe OTP fields
    users_col.update_one(
        {"_id": req.user_id},
        {"$set": {"status": "active"}, "$unset": {"otp": "", "otp_expiry": ""}}
    )

    # Generate session JWT
    token = create_jwt_token({"sub": req.user_id, "username": user.get("username")})
    _set_session_cookie(response, token)

    return {
        "status": "success",
        "username": user.get("username"),
        "display_name": _user_display_name(user),
        "preferences": _user_preferences(user),
        "user_id": req.user_id,
    }

def _finalize_registration_from_pending(
    pending: dict,
    region_override: Optional[str] = None,
    response: Optional[Response] = None,
) -> dict:
    _ensure_user_indexes()
    username = pending["username"]
    if "first_name" in pending or "last_name" in pending:
        first_name, last_name, display_name = _resolve_identity(
            pending.get("first_name"),
            pending.get("last_name"),
            pending.get("display_name"),
        )
    else:
        # Let registrations already awaiting their email OTP complete without
        # overwriting their legacy name; they can complete the new split fields later.
        display_name = re.sub(r"\s+", " ", (pending.get("display_name") or "").strip())
        first_name, last_name = _split_legacy_display_name(display_name)
    phone_e164 = pending.get("phone_e164") or pending.get("phone", "")
    phone_number = pending.get("phone_number", "")
    country_code = pending.get("country_code", "")
    country_iso = pending.get("country_iso", "")
    region = _clean_region(region_override or pending.get("region") or "Malaysia")

    if users_col.find_one({"username": username, "status": "active"}):
        pending_otps_col.delete_one({"_id": pending["_id"]})
        raise HTTPException(status_code=400, detail="Username already taken")
    if phone_e164 and (users_col.find_one({"phone_e164": phone_e164, "status": "active"}) or users_col.find_one({"phone": phone_e164, "status": "active"})):
        pending_otps_col.delete_one({"_id": pending["_id"]})
        raise HTTPException(status_code=400, detail="Phone number already registered")

    user_id = str(uuid.uuid4())
    user_doc = {
        "_id": user_id,
        "username": username,
        "name": display_name,
        "display_name": display_name,
        "first_name": first_name,
        "last_name": last_name,
        "phone": _legacy_phone_value(phone_e164),
        "country_code": country_code,
        "country_iso": country_iso,
        "phone_number": phone_number,
        "phone_e164": phone_e164,
        "phone_verified_at": None,
        "region": region,
        "profile_completed": True,
        "preferences": pending.get("preferences") or {"language": "en"},
        "password": pending["password"],
        "status": "active",
        "auth_provider": "local",
        "registration_method": pending.get("registration_method") or "email",
        "created_at": datetime.utcnow(),
        "verified_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    users_col.insert_one(user_doc)
    pending_otps_col.delete_one({"_id": pending["_id"]})
    token = create_jwt_token({"sub": str(user_id), "username": username})
    if response is not None:
        _set_session_cookie(response, token)
    created_user = users_col.find_one({"_id": user_id}) or user_doc
    return {
        "status": "success",
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone_e164,
        "country_code": country_code,
        "country_iso": country_iso,
        "phone_number": phone_number,
        "phone_e164": phone_e164,
        "phone_verified_at": None,
        "region": region,
        "display_name": _user_display_name(created_user),
        "preferences": _user_preferences(created_user),
        "user_id": str(user_id),
    }

@auth_router.post("/api/resend-otp")
async def resend_otp(req: ResendOTPRequest, request: Request):
    _ensure_pending_otp_indexes()
    pending = pending_otps_col.find_one({"_id": req.user_id})
    if not pending:
        raise HTTPException(status_code=404, detail="Pending verification not found. Please register again.")

    username = pending["username"]
    if users_col.find_one({"username": username, "status": "active"}):
        pending_otps_col.delete_one({"_id": req.user_id})
        raise HTTPException(status_code=400, detail="Username already verified")

    now = datetime.utcnow()
    retry_after = _otp_resend_retry_after_seconds(pending, now)
    if retry_after:
        raise _otp_resend_rate_limited_error(retry_after)

    otp_code = generate_otp()
    current_time = _current_time_for_email()
    device_info = _device_info(request)
    ip_address = _client_ip(request)
    language = _normalize_language(pending.get("language"))
    resend_available_at = now + timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS)

    # Claim the resend atomically before sending. This blocks concurrent tabs
    # and rapid repeated clicks even when the front end is bypassed.
    claimed = pending_otps_col.update_one(
        {
            "_id": req.user_id,
            "$or": [
                {"resend_available_at": {"$exists": False}},
                {"resend_available_at": {"$lte": now}},
            ],
        },
        {"$set": {
            "otp": get_password_hash(otp_code),
            "created_at": now,
            "last_otp_sent_at": now,
            "resend_available_at": resend_available_at,
            "expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
            "current_time": current_time,
            "device_info": device_info,
            "ip_address": ip_address,
            "language": language,
        }}
    )
    if claimed.matched_count != 1:
        refreshed_pending = pending_otps_col.find_one({"_id": req.user_id})
        if not refreshed_pending:
            raise HTTPException(status_code=404, detail="Pending verification not found. Please register again.")
        raise _otp_resend_rate_limited_error(_otp_resend_retry_after_seconds(refreshed_pending, now) or OTP_RESEND_COOLDOWN_SECONDS)

    try:
        send_otp_email(username, otp_code, current_time, device_info, ip_address, language)
    except Exception as exc:
        # The code was not delivered, so allow a fresh request rather than
        # trapping the person in a cooldown caused by a mail-provider failure.
        pending_otps_col.update_one(
            {"_id": req.user_id, "resend_available_at": resend_available_at},
            {"$set": {"resend_available_at": now}},
        )
        _raise_otp_email_delivery_error(exc)

    return {
        "status": "resent",
        "user_id": req.user_id,
        "resend_available_in": OTP_RESEND_COOLDOWN_SECONDS,
    }


def _password_reset_pending_response(reset_id: str, resend_available_in: int) -> dict:
    """Use one response shape so reset requests do not reveal account existence."""
    return {
        "status": "pending",
        "reset_id": reset_id,
        "resend_available_in": max(0, int(resend_available_in)),
    }


def _resend_password_reset_otp(
    reset_id: str,
    request: Request,
    language_override: Optional[str] = None,
) -> Optional[int]:
    """Atomically rotate and deliver an existing password-reset OTP.

    Returns the cooldown in seconds, or ``None`` when the supplied opaque ID
    does not map to a reset request. The latter lets the public endpoint avoid
    becoming an account-enumeration oracle.
    """
    pending = pending_otps_col.find_one({"_id": reset_id, "type": "password_reset"})
    if not pending:
        return None

    now = datetime.utcnow()
    if now > pending.get("expires_at", datetime.min):
        pending_otps_col.delete_one({"_id": reset_id, "type": "password_reset"})
        raise HTTPException(status_code=400, detail="This password reset code has expired. Start a new reset request.")

    retry_after = _otp_resend_retry_after_seconds(pending, now)
    if retry_after:
        raise _otp_resend_rate_limited_error(retry_after)

    otp_code = generate_otp()
    current_time = _current_time_for_email()
    device_info = _device_info(request)
    ip_address = _client_ip(request)
    language = _normalize_language(language_override or pending.get("language"))
    resend_available_at = now + timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS)

    # Claim this send before contacting SMTP. This protects against concurrent
    # tabs and direct API calls in exactly the same way as registration OTP.
    claimed = pending_otps_col.update_one(
        {
            "_id": reset_id,
            "type": "password_reset",
            "$or": [
                {"resend_available_at": {"$exists": False}},
                {"resend_available_at": {"$lte": now}},
            ],
        },
        {"$set": {
            "otp": get_password_hash(otp_code),
            "created_at": now,
            "last_otp_sent_at": now,
            "resend_available_at": resend_available_at,
            "expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
            "current_time": current_time,
            "device_info": device_info,
            "ip_address": ip_address,
            "language": language,
        }, "$unset": {
            "reset_token_hash": "",
            "reset_token_expires_at": "",
            "verified_at": "",
        }},
    )
    if claimed.matched_count != 1:
        refreshed = pending_otps_col.find_one({"_id": reset_id, "type": "password_reset"})
        if not refreshed:
            return None
        raise _otp_resend_rate_limited_error(
            _otp_resend_retry_after_seconds(refreshed, now) or OTP_RESEND_COOLDOWN_SECONDS
        )

    try:
        send_otp_email(
            pending["username"], otp_code, current_time, device_info, ip_address,
            language, purpose="password_reset",
        )
    except Exception as exc:
        # The code was never delivered, so do not trap the person in a local
        # cooldown caused by the mail provider.
        pending_otps_col.update_one(
            {"_id": reset_id, "type": "password_reset", "resend_available_at": resend_available_at},
            {"$set": {"resend_available_at": now}},
        )
        _raise_otp_email_delivery_error(exc)

    return OTP_RESEND_COOLDOWN_SECONDS


@auth_router.post("/api/password-reset/request")
async def request_password_reset(req: PasswordResetRequest, request: Request):
    """Send an email OTP for a password reset without exposing account lookup."""
    _ensure_pending_otp_indexes()
    email = _normalize_email(req.email)
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="Enter a valid email address")

    user = users_col.find_one({"username": email, "status": "active"})
    if not user:
        # Keep the response indistinguishable from an existing account. The ID
        # is opaque and has no backing record, so it cannot reset anything.
        return _password_reset_pending_response(str(uuid.uuid4()), OTP_RESEND_COOLDOWN_SECONDS)

    now = datetime.utcnow()
    pending = pending_otps_col.find_one({"type": "password_reset", "username": email})
    if pending and now > pending.get("expires_at", datetime.min):
        pending_otps_col.delete_one({"_id": pending["_id"], "type": "password_reset"})
        pending = None

    if pending and pending.get("reset_token_hash"):
        # A verified reset is intentionally short lived. Starting again should
        # invalidate it rather than leaving two usable reset paths.
        pending_otps_col.delete_one({"_id": pending["_id"], "type": "password_reset"})
        pending = None

    if pending:
        retry_after = _otp_resend_retry_after_seconds(pending, now)
        if retry_after:
            return _password_reset_pending_response(pending["_id"], retry_after)
        cooldown = _resend_password_reset_otp(pending["_id"], request, req.language)
        return _password_reset_pending_response(pending["_id"], cooldown or OTP_RESEND_COOLDOWN_SECONDS)

    reset_id = str(uuid.uuid4())
    otp_code = generate_otp()
    current_time = _current_time_for_email()
    device_info = _device_info(request)
    ip_address = _client_ip(request)
    language = _normalize_language(req.language)
    reset_doc = {
        "_id": reset_id,
        "type": "password_reset",
        "username": email,
        "user_id": str(user["_id"]),
        "otp": get_password_hash(otp_code),
        "created_at": now,
        "last_otp_sent_at": now,
        "resend_available_at": now + timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS),
        "expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
        "language": language,
        "current_time": current_time,
        "device_info": device_info,
        "ip_address": ip_address,
    }
    try:
        pending_otps_col.insert_one(reset_doc)
    except DuplicateKeyError:
        # Another tab claimed this email first. Reuse its cooldown and avoid a
        # duplicate delivery rather than racing to replace the code.
        existing = pending_otps_col.find_one({"type": "password_reset", "username": email})
        if existing:
            return _password_reset_pending_response(
                existing["_id"], _otp_resend_retry_after_seconds(existing, now)
            )
        raise

    try:
        send_otp_email(
            email, otp_code, current_time, device_info, ip_address,
            language, purpose="password_reset",
        )
    except Exception as exc:
        pending_otps_col.delete_one({"_id": reset_id, "type": "password_reset"})
        _raise_otp_email_delivery_error(exc)

    return _password_reset_pending_response(reset_id, OTP_RESEND_COOLDOWN_SECONDS)


@auth_router.post("/api/password-reset/resend")
async def resend_password_reset_otp(req: ResendPasswordResetOTPRequest, request: Request):
    cooldown = _resend_password_reset_otp(req.reset_id, request)
    if cooldown is None:
        return {"status": "resent", "resend_available_in": OTP_RESEND_COOLDOWN_SECONDS}
    return {"status": "resent", "resend_available_in": cooldown}


@auth_router.post("/api/password-reset/verify")
async def verify_password_reset_otp(req: VerifyPasswordResetOTPRequest):
    pending = pending_otps_col.find_one({"_id": req.reset_id, "type": "password_reset"})
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")
    if datetime.utcnow() > pending.get("expires_at", datetime.min):
        pending_otps_col.delete_one({"_id": req.reset_id, "type": "password_reset"})
        raise HTTPException(status_code=400, detail="This password reset code has expired. Start a new reset request.")
    if not verify_password(req.otp, pending.get("otp", "")):
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    reset_token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    reset_token_expires_at = now + timedelta(minutes=OTP_TTL_MINUTES)
    claimed = pending_otps_col.update_one(
        {"_id": req.reset_id, "type": "password_reset", "otp": pending.get("otp", "")},
        {"$set": {
            "verified_at": now,
            "reset_token_hash": hashlib.sha256(reset_token.encode("utf-8")).hexdigest(),
            "reset_token_expires_at": reset_token_expires_at,
            # The TTL index uses expires_at. Extend it to match the short
            # post-verification password-entry window.
            "expires_at": reset_token_expires_at,
        }, "$unset": {"otp": ""}},
    )
    if claimed.matched_count != 1:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")
    return {"status": "verified", "reset_token": reset_token}


@auth_router.post("/api/password-reset/complete")
async def complete_password_reset(req: CompletePasswordResetRequest):
    _validate_new_password(req.new_password)
    pending = pending_otps_col.find_one({"_id": req.reset_id, "type": "password_reset"})
    expected_token_hash = hashlib.sha256(req.reset_token.encode("utf-8")).hexdigest()
    if (
        not pending
        or not pending.get("reset_token_hash")
        or not secrets.compare_digest(pending["reset_token_hash"], expected_token_hash)
        or datetime.utcnow() > pending.get("reset_token_expires_at", datetime.min)
    ):
        raise HTTPException(status_code=400, detail="Your password reset session has expired. Start again to receive a new code.")

    password_changed_at = datetime.utcnow()
    result = users_col.update_one(
        {"_id": pending.get("user_id"), "username": pending.get("username"), "status": "active"},
        {"$set": {
            "password": get_password_hash(req.new_password),
            "password_changed_at": password_changed_at,
            "password_changed_at_epoch": int(time.time()),
            "updated_at": password_changed_at,
        }},
    )
    if result.matched_count != 1:
        pending_otps_col.delete_one({"_id": req.reset_id, "type": "password_reset"})
        raise HTTPException(status_code=400, detail="Your password reset session has expired. Start again to receive a new code.")

    pending_otps_col.delete_one({
        "_id": req.reset_id,
        "type": "password_reset",
        "reset_token_hash": expected_token_hash,
    })
    return {"status": "success"}

@auth_router.post("/api/login")
async def login(req: AuthRequest, response: Response):
    _ensure_user_indexes()
    login_id = req.username.strip()
    user = users_col.find_one({"username": login_id.lower()})

    if not user or not verify_password(req.password, user.get("password", "")):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    new_password_hash = maybe_upgrade_password_hash(req.password, user.get("password", ""))
    if new_password_hash:
        users_col.update_one(
            {"_id": user["_id"]},
            {"$set": {"password": new_password_hash}}
        )
        user["password"] = new_password_hash

    if user.get("status") == "unverified":
        # Block login. They must verify.
        # We can optionally issue a new OTP here, but let's just tell the client to redirect.
        return JSONResponse(status_code=403, content={
            "status": "unverified",
            "detail": "Account not verified. Please check your email.",
            "user_id": str(user["_id"])
        })

    display_name = _user_display_name(user)
    if not user.get("display_name"):
        users_col.update_one({"_id": user["_id"]}, {"$set": {"display_name": display_name}})
        user["display_name"] = display_name
    users_col.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": datetime.utcnow(), "updated_at": datetime.utcnow()}})

    token = create_jwt_token({"sub": str(user["_id"]), "username": user.get("username")})
    _set_session_cookie(response, token)

    return {
        "status": "success",
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "phone": user.get("phone_e164") or user.get("phone"),
        "country_code": user.get("country_code") or user.get("phone_country_code"),
        "country_iso": user.get("country_iso"),
        "phone_number": user.get("phone_number"),
        "phone_e164": user.get("phone_e164") or user.get("phone"),
        "phone_verified_at": user.get("phone_verified_at"),
        "display_name": display_name,
        "preferences": _user_preferences(user),
        "user_id": str(user["_id"]),
    }

# ─── Third-Party OAuth Placeholders ────────────────────

import urllib.request
import json

@auth_router.post("/api/auth/google")
async def google_auth(req: OAuthRequest, response: Response):
    _ensure_user_indexes()
    # Retrieve User Info via Google Access Token
    try:
        url = "https://www.googleapis.com/oauth2/v3/userinfo"
        req_auth = urllib.request.Request(url, headers={"Authorization": f"Bearer {req.token}"})
        with urllib.request.urlopen(req_auth) as google_response:
            user_info = json.loads(google_response.read().decode())
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    google_sub = user_info.get("sub")
    email = _normalize_email(user_info.get("email"))
    first_name, last_name, name, has_complete_name = _identity_from_google_profile(user_info, email)
    picture = user_info.get("picture")
    preferences = {
        "language": _normalize_language(req.language),
    }

    if not google_sub or not email:
        raise HTTPException(status_code=400, detail="Incomplete Google profile")

    # Associate or create the account using OpenAI-style identity rules:
    # social login can combine with an email/password account only when the email matches.
    user = users_col.find_one({"auth_provider_id": google_sub})
    if user:
        email = _ensure_google_email_can_attach(user, email)
    else:
        user = users_col.find_one({"username": email})

    if not user:
        # Register new social user
        user_id = str(uuid.uuid4())
        user_doc = {
            "_id": user_id,
            "username": email,
            "name": name,
            "display_name": name,
            "first_name": first_name,
            "last_name": last_name,
            "preferences": preferences,
            "status": "active",
            "auth_provider": "google",
            "auth_provider_id": google_sub,
            "google_email": email,
            "registration_method": "google",
            "profile_completed": has_complete_name,
            "picture": picture,
            "created_at": datetime.utcnow(),
            "verified_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "last_login_at": datetime.utcnow(),
        }
        users_col.insert_one(user_doc)
        token = create_jwt_token({"sub": user_id, "username": email})
        _set_session_cookie(response, token)
        requires_profile_completion = _requires_profile_completion(user_doc)
        return {
            "status": "success",
            "username": email,
            "is_new_user": True,
            "profile_completion_page": "register" if requires_profile_completion else None,
            "requires_profile_completion": requires_profile_completion,
            "first_name": first_name,
            "last_name": last_name,
            "display_name": name,
            "preferences": preferences,
            "user_id": user_id,
            "avatarUrl": picture,
        }
    else:
        # User exists, optionally link google ID if log in via email previously
        updates = {}
        existing_google_sub = user.get("auth_provider_id")
        if existing_google_sub and existing_google_sub != google_sub:
            raise HTTPException(status_code=409, detail="This email is already linked to a different Google account")
        if not existing_google_sub:
            updates.update({"auth_provider": "google", "auth_provider_id": google_sub, "google_email": email, "status": "active"})
        if picture and user.get("picture") != picture:
            updates.update({"picture": picture})
            user["picture"] = picture
        if not user.get("display_name") and name:
            updates.update({"display_name": name})
            user["display_name"] = name
        if not user.get("first_name") and first_name:
            updates["first_name"] = first_name
            user["first_name"] = first_name
        if not user.get("last_name") and last_name:
            updates["last_name"] = last_name
            user["last_name"] = last_name
        if not user.get("preferences"):
            updates.update({"preferences": preferences})
            user["preferences"] = preferences
        updates["profile_completed"] = not _requires_profile_completion({**user, **updates})
        updates.update({"last_login_at": datetime.utcnow(), "updated_at": datetime.utcnow()})

        if updates:
            users_col.update_one({"_id": user["_id"]}, {"$set": updates})
            user.update(updates)

        token = create_jwt_token({"sub": str(user["_id"]), "username": user.get("username")})
        _set_session_cookie(response, token)
        return {
            "status": "success",
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "phone": user.get("phone_e164") or user.get("phone"),
            "country_code": user.get("country_code") or user.get("phone_country_code"),
            "country_iso": user.get("country_iso"),
            "phone_number": user.get("phone_number"),
            "phone_e164": user.get("phone_e164") or user.get("phone"),
            "phone_verified_at": user.get("phone_verified_at"),
            "region": user.get("region"),
            "is_new_user": False,
            "registration_method": user.get("registration_method"),
            "profile_completion_page": _profile_completion_page(user),
            "requires_profile_completion": _requires_profile_completion(user),
            "display_name": _user_display_name(user),
            "preferences": _user_preferences(user),
            "user_id": str(user["_id"]),
            "avatarUrl": user.get("picture"),
        }

@auth_router.get("/api/account/preferences")
async def get_account_preferences(request: Request):
    user = _auth_user(request)
    auth_provider = user.get("auth_provider", "local")
    google_linked = bool(user.get("auth_provider_id"))
    google_email = user.get("google_email") or (user.get("username") if auth_provider == "google" else None)
    return {
        "status": "success",
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "phone": user.get("phone_e164") or user.get("phone"),
        "country_code": user.get("country_code") or user.get("phone_country_code"),
        "country_iso": user.get("country_iso"),
        "phone_number": user.get("phone_number"),
        "phone_e164": user.get("phone_e164") or user.get("phone"),
        "phone_verified_at": user.get("phone_verified_at"),
        "region": user.get("region"),
        "profile_completed": not _requires_profile_completion(user),
        "profile_completion_page": _profile_completion_page(user),
        "requires_profile_completion": _requires_profile_completion(user),
        "registration_method": user.get("registration_method"),
        "display_name": _user_display_name(user),
        "avatarUrl": user.get("picture"),
        "created_at": user.get("created_at").isoformat() if user.get("created_at") else None,
        "preferences": _user_preferences(user),
        "has_password": bool(user.get("password")),
        "auth_provider": auth_provider,
        "google_linked": google_linked,
        "google_email": google_email,
    }

@auth_router.put("/api/account/preferences")
async def update_account_preferences(req: PreferencesRequest, request: Request):
    user = _auth_user(request)
    current = _user_preferences(user)
    if req.language is not None:
        current["language"] = _normalize_language(req.language)
    users_col.update_one(
        {"_id": user["_id"]},
        {"$set": {"preferences": current, "updated_at": datetime.utcnow()}}
    )
    return {"status": "success", "preferences": current}

@auth_router.delete("/api/account")
async def delete_account(request: Request):
    user = _auth_user(request)
    user_id = str(user["_id"])
    db["feedbacks"].delete_many({"user_id": user_id})
    user_chats = list(db["chats"].find({"user_id": user_id}, {"_id": 1}))
    chat_ids = [chat["_id"] for chat in user_chats]
    if chat_ids:
        db["feedbacks"].delete_many({"chat_id": {"$in": chat_ids}})
    db["chats"].delete_many({"user_id": user_id})
    pending_otps_col.delete_many({"username": user.get("username")})
    users_col.delete_one({"_id": user_id})
    return {"status": "deleted"}

@auth_router.put("/api/account/profile")
async def update_profile(req: UpdateProfileRequest, request: Request):
    user = _auth_user(request)
    if req.first_name is None and req.last_name is None:
        # The account settings screen still sends display_name during the
        # migration window. Keep that endpoint backward compatible.
        display_name = re.sub(r"\s+", " ", (req.display_name or "").strip())
        if not display_name:
            raise HTTPException(status_code=400, detail="Display name cannot be empty")
        if len(display_name) > 80:
            raise HTTPException(status_code=400, detail="Display name too long (max 80 chars)")
        updates = {"name": display_name, "display_name": display_name, "updated_at": datetime.utcnow()}
        users_col.update_one({"_id": user["_id"]}, {"$set": updates})
        return {
            "status": "success",
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "display_name": display_name,
        }
    first_name, last_name, display_name = _resolve_identity(req.first_name, req.last_name, req.display_name)
    users_col.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "first_name": first_name,
            "last_name": last_name,
            "name": display_name,
            "display_name": display_name,
            "updated_at": datetime.utcnow(),
        }}
    )
    return {"status": "success", "first_name": first_name, "last_name": last_name, "display_name": display_name}

@auth_router.post("/api/phone/validate")
async def validate_phone(req: PhoneValidationRequest):
    return {"status": "success", **_validate_phone_number(req.country_iso, req.phone_number)}

@auth_router.put("/api/account/complete-google-profile")
async def complete_google_profile(req: CompleteGoogleProfileRequest, request: Request):
    _ensure_user_indexes()
    user = _auth_user(request)
    first_name, last_name, display_name = _resolve_identity(req.first_name, req.last_name, req.display_name)
    phone_data = _validate_phone_number(req.country_iso, req.phone_number)
    region = _clean_region(req.region)

    conflict = users_col.find_one({
        "phone_e164": phone_data["phone_e164"],
        "status": "active",
        "_id": {"$ne": user["_id"]},
    }) or users_col.find_one({
        "phone": phone_data["phone_e164"],
        "status": "active",
        "_id": {"$ne": user["_id"]},
    })
    if conflict:
        raise HTTPException(status_code=400, detail="Phone number already registered")

    updates = {
        "name": display_name,
        "display_name": display_name,
        "first_name": first_name,
        "last_name": last_name,
        "phone": _legacy_phone_value(phone_data["phone_e164"]),
        "country_code": phone_data["country_code"],
        "country_iso": phone_data["country_iso"],
        "phone_number": phone_data["phone_number"],
        "phone_e164": phone_data["phone_e164"],
        "phone_verified_at": user.get("phone_verified_at"),
        "region": region,
        "profile_completed": True,
        "updated_at": datetime.utcnow(),
    }
    users_col.update_one({"_id": user["_id"]}, {"$set": updates})
    return {
        "status": "success",
        "first_name": first_name,
        "last_name": last_name,
        "display_name": display_name,
        "phone": phone_data["phone_e164"],
        "country_code": phone_data["country_code"],
        "country_iso": phone_data["country_iso"],
        "phone_number": phone_data["phone_number"],
        "phone_e164": phone_data["phone_e164"],
        "phone_verified_at": user.get("phone_verified_at"),
        "region": region,
        "requires_profile_completion": False,
    }


@auth_router.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("msme_session", path="/")
    return {"status": "success"}

@auth_router.post("/api/account/send-email-otp")
async def send_account_email_otp(req: SendEmailOTPRequest, request: Request):
    user = _auth_user(request)
    new_email = req.new_email.strip().lower()
    if "@" not in new_email or "." not in new_email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Invalid email address")
    conflict = users_col.find_one({"username": new_email})
    if conflict and str(conflict["_id"]) != str(user["_id"]):
        raise HTTPException(status_code=409, detail="Email already in use")

    pending_id = str(uuid.uuid4())
    otp_code = generate_otp()
    current_time = _current_time_for_email()
    device_info = _device_info(request)
    ip_address = _client_ip(request)
    lang = _user_preferences(user)["language"]

    _ensure_pending_otp_indexes()
    now = datetime.utcnow()
    pending_otps_col.delete_many({"type": "email_change", "user_id": str(user["_id"])})
    pending_otps_col.insert_one({
        "_id": pending_id,
        "type": "email_change",
        "user_id": str(user["_id"]),
        "new_email": new_email,
        "otp": get_password_hash(otp_code),
        "created_at": now,
        "expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
        "language": lang,
    })

    try:
        send_otp_email(new_email, otp_code, current_time, device_info, ip_address, lang)
    except Exception as exc:
        pending_otps_col.delete_one({"_id": pending_id})
        _raise_otp_email_delivery_error(exc)

    return {"status": "pending", "pending_id": pending_id}

@auth_router.put("/api/account/email")
async def update_email(req: UpdateEmailWithOTPRequest, request: Request):
    user = _auth_user(request)
    pending = pending_otps_col.find_one({
        "_id": req.pending_id,
        "type": "email_change",
        "user_id": str(user["_id"])
    })
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")
    if datetime.utcnow() > pending["expires_at"]:
        pending_otps_col.delete_one({"_id": req.pending_id})
        raise HTTPException(status_code=400, detail="Verification code expired")
    if not verify_password(req.otp, pending.get("otp", "")):
        raise HTTPException(status_code=400, detail="Invalid verification code")

    new_email = pending["new_email"]
    conflict = users_col.find_one({"username": new_email})
    if conflict and str(conflict["_id"]) != str(user["_id"]):
        pending_otps_col.delete_one({"_id": req.pending_id})
        raise HTTPException(status_code=409, detail="Email already in use by another account")

    users_col.update_one(
        {"_id": user["_id"]},
        {"$set": {"username": new_email, "updated_at": datetime.utcnow()}}
    )
    pending_otps_col.delete_one({"_id": req.pending_id})
    return {"status": "success", "username": new_email}

@auth_router.post("/api/account/download-data")
async def download_account_data(request: Request):
    user = _auth_user(request)
    user_id = str(user["_id"])
    email = user.get("username", "")
    if not email:
        raise HTTPException(status_code=400, detail="No email address on file")

    lines = []
    lines.append("=== bisnes.ai — Account Data Export ===")
    lines.append(f"Export Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("--- Personal Information ---")
    lines.append(f"Display Name : {user.get('display_name', '')}")
    lines.append(f"Email        : {email}")
    lines.append(f"Phone        : {user.get('phone', '')}")
    lines.append(f"Phone Code   : {user.get('phone_country_code', '')}")
    lines.append(f"Region       : {user.get('region', '')}")
    created = user.get("created_at")
    lines.append(f"Account Created: {created.strftime('%Y-%m-%d') if isinstance(created, datetime) else str(created or 'Unknown')}")
    lines.append(f"Auth Provider  : {user.get('auth_provider', 'local')}")
    lines.append("")
    lines.append("--- Chat History ---")

    chats = list(db["chats"].find({"user_id": user_id}).sort("updated_at", -1).limit(300))
    if not chats:
        lines.append("(No chat history found)")
    else:
        for chat in chats:
            updated = chat.get("updated_at")
            date_str = updated.strftime("%Y-%m-%d") if isinstance(updated, datetime) else ""
            lines.append(f"\n[{chat.get('title', 'Untitled')} | {date_str}]")
            for msg in chat.get("messages", []):
                role = msg.get("role", "").upper()
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
                content_str = str(content)[:800].replace("\n", " ")
                lines.append(f"  [{role}]: {content_str}")

    txt_content = "\n".join(lines)

    msg_out = MIMEMultipart()
    msg_out["Subject"] = "Your bisnes.ai Account Data Export"
    msg_out["From"] = formataddr((SMTP_FROM_NAME, SMTP_USERNAME))
    msg_out["To"] = email
    msg_out.attach(MIMEText("Please find your account data export attached.", "plain", "utf-8"))
    attachment = MIMEText(txt_content, "plain", "utf-8")
    attachment.add_header("Content-Disposition", "attachment", filename="bisnes_ai_data_export.txt")
    msg_out.attach(attachment)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as srv:
        srv.ehlo()
        srv.starttls(context=context)
        srv.login(SMTP_USERNAME, SMTP_APP_PASSWORD)
        srv.sendmail(SMTP_USERNAME, [email], msg_out.as_string())

    return {"status": "success"}

@auth_router.post("/api/account/link-google")
async def link_google(req: LinkGoogleRequest, request: Request):
    user = _auth_user(request)
    try:
        url = "https://www.googleapis.com/oauth2/v3/userinfo"
        req_auth = urllib.request.Request(url, headers={"Authorization": f"Bearer {req.token}"})
        with urllib.request.urlopen(req_auth) as response:
            user_info = json.loads(response.read().decode())
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    google_sub = user_info.get("sub")
    google_email = _normalize_email(user_info.get("email"))
    picture = user_info.get("picture")

    if not google_sub or not google_email:
        raise HTTPException(status_code=400, detail="Incomplete Google profile")

    google_email = _ensure_google_email_can_attach(user, google_email)

    existing = users_col.find_one({"auth_provider_id": google_sub})
    if existing and str(existing["_id"]) != str(user["_id"]):
        raise HTTPException(status_code=409, detail="This Google account is already linked to another account")
    if user.get("auth_provider_id") and user.get("auth_provider_id") != google_sub:
        raise HTTPException(status_code=409, detail="A different Google account is already linked to this account")

    updates = {"auth_provider_id": google_sub, "google_email": google_email, "updated_at": datetime.utcnow()}
    if picture:
        updates["picture"] = picture
    users_col.update_one({"_id": user["_id"]}, {"$set": updates})

    return {"status": "success", "google_linked": True, "google_email": google_email, "auth_provider": user.get("auth_provider", "local")}

@auth_router.post("/api/account/unlink-google")
async def unlink_google(request: Request):
    user = _auth_user(request)
    if not user.get("auth_provider_id"):
        raise HTTPException(status_code=400, detail="No Google account linked")
    if not user.get("password"):
        raise HTTPException(status_code=400, detail="Set a password first before unlinking Google")
    users_col.update_one(
        {"_id": user["_id"]},
        {
            "$set": {"auth_provider": "local", "updated_at": datetime.utcnow()},
            "$unset": {
                "auth_provider_id": "",
                "google_email": "",
                "google_creds_drive": "",
                "google_creds_gmail": "",
                "google_creds_docs": "",
                "google_creds_calendar": "",
                "google_creds_meet": "",
                "google_creds_sheets": "",
                "google_token": "",
                "google_refresh_token": "",
                "google_scopes": "",
                "google_token_expiry": "",
                "google_token_uri": "",
                "google_client_id": "",
                "google_client_secret": "",
                "google_token_updated_at": "",
                "google_access_token": "",
            }
        }
    )
    return {"status": "success", "google_linked": False}

@auth_router.put("/api/account/password")
async def set_password(req: SetPasswordRequest, request: Request):
    user = _auth_user(request)
    _validate_new_password(req.new_password)
    hashed = get_password_hash(req.new_password)
    users_col.update_one(
        {"_id": user["_id"]},
        {"$set": {"password": hashed, "updated_at": datetime.utcnow()}}
    )
    return {"status": "success", "has_password": True}
