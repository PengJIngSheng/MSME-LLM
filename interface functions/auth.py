import os
import uuid
import html
import hashlib
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo import MongoClient
from typing import Optional
import bcrypt
import jwt

# JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-pepper-key-2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# Connect to MongoDB
mongo_client = MongoClient("mongodb://localhost:27017/")
db = mongo_client["pepper_chat_db"]
users_col = db["users"]
pending_otps_col = db["pending_otps"]

# SMTP Configuration
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "yynarrator@gmail.com")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "frmw kwci hgjc bhey")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Ministry of Finance")
PUBLIC_SITE_URL = os.getenv("PUBLIC_SITE_URL", "http://localhost:8000")
OTP_TTL_MINUTES = 10
_pending_otp_indexes_ready = False

auth_router = APIRouter()

# ─── Pydantic Models ───────────────────────────────────
class AuthRequest(BaseModel):
    username: str
    password: str
    language: Optional[str] = "en"
    theme: Optional[str] = "dark"

class VerifyOTPRequest(BaseModel):
    user_id: str
    otp: str

class ResendOTPRequest(BaseModel):
    user_id: str

class OAuthRequest(BaseModel):
    token: str
    language: Optional[str] = "en"
    theme: Optional[str] = "dark"

class PreferencesRequest(BaseModel):
    language: Optional[str] = None
    theme: Optional[str] = None

class UpdateProfileRequest(BaseModel):
    display_name: str

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

def create_jwt_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

def generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"

def generate_display_name(username: str = "") -> str:
    suffix = secrets.token_hex(2).upper()
    return f"MOF Member {suffix}"

def _user_display_name(user: dict | None) -> str:
    if not user:
        return ""
    display_name = (user.get("display_name") or user.get("name") or "").strip()
    if display_name:
        return display_name
    username = (user.get("username") or "").strip()
    if "@" in username:
        return username.split("@")[0]
    return username or generate_display_name()

def _normalize_theme(theme: Optional[str]) -> str:
    normalized = (theme or "dark").strip().lower()
    return normalized if normalized in {"dark", "light"} else "dark"

def _user_preferences(user: dict | None) -> dict:
    prefs = (user or {}).get("preferences") or {}
    return {
        "language": _normalize_language(prefs.get("language") or (user or {}).get("language") or "en"),
        "theme": _normalize_theme(prefs.get("theme") or (user or {}).get("theme") or "dark"),
    }

def _auth_user(request: Request) -> dict:
    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing authorization token")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    user = users_col.find_one({"_id": str(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

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
        "subject": "邮箱注册验证码",
        "title": "邮箱注册验证码",
        "intro": "我们注意到您于 {current_time} 使用设备 {device_info} 发起 MOF 邮箱注册请求，IP 地址为 {ip_address}。",
        "validity": "此验证码为邮箱注册验证码。验证码在 10 分钟内有效，请勿泄露。如为您本人操作，则无需其他操作。",
        "support": "如果这不是您本人发起的请求，您可以通过 support@mof.gov.my 联系客服团队。",
        "button": "查看官网",
        "uid": "UID",
        "footer": "Ministry of Finance 安全通知。请勿回复此邮件。",
        "plain_code": "验证码",
        "plain_validity": "此验证码在 10 分钟内有效。",
    },
    "en": {
        "subject": "Email registration verification code",
        "title": "Email registration verification code",
        "intro": "We noticed an MOF email registration request at {current_time} from this device: {device_info}. The request IP was {ip_address}.",
        "validity": "This code verifies your email registration. It is valid for 10 minutes. Please do not share it with anyone. If this was you, no further action is required.",
        "support": "If this was not you, contact our support team at support@mof.gov.my.",
        "button": "View Official Website",
        "uid": "UID",
        "footer": "Ministry of Finance security notification. Please do not reply to this email.",
        "plain_code": "Verification code",
        "plain_validity": "This code is valid for 10 minutes.",
    },
    "ms": {
        "subject": "Kod pengesahan pendaftaran e-mel",
        "title": "Kod pengesahan pendaftaran e-mel",
        "intro": "Kami mengesan permintaan pendaftaran e-mel MOF pada {current_time} daripada peranti ini: {device_info}. IP permintaan ialah {ip_address}.",
        "validity": "Kod ini digunakan untuk mengesahkan pendaftaran e-mel anda. Kod sah selama 10 minit. Jangan kongsikan kod ini dengan sesiapa. Jika ini anda, tiada tindakan lanjut diperlukan.",
        "support": "Jika ini bukan anda, hubungi pasukan sokongan kami di support@mof.gov.my.",
        "button": "Lihat Laman Rasmi",
        "uid": "UID",
        "footer": "Notifikasi keselamatan Ministry of Finance. Jangan balas e-mel ini.",
        "plain_code": "Kod pengesahan",
        "plain_validity": "Kod ini sah selama 10 minit.",
    },
}

def _normalize_language(language: Optional[str]) -> str:
    lang = (language or "en").strip().lower()
    return lang if lang in OTP_EMAIL_COPY else "en"

def build_otp_email_html(
    otp_code: str,
    current_time: str,
    device_info: str,
    ip_address: str,
    language: Optional[str] = "en",
) -> str:
    lang = _normalize_language(language)
    copy = OTP_EMAIL_COPY[lang]
    safe_otp = html.escape(otp_code)
    safe_time = html.escape(current_time)
    safe_device = html.escape(device_info)
    safe_ip = html.escape(ip_address)
    safe_intro = html.escape(copy["intro"].format(
        current_time=current_time,
        device_info=device_info,
        ip_address=ip_address,
    ))
    safe_validity = html.escape(copy["validity"])
    safe_support = html.escape(copy["support"])
    safe_title = html.escape(copy["title"])
    safe_button = html.escape(copy["button"])
    safe_uid = html.escape(copy["uid"])
    safe_footer = html.escape(copy["footer"])
    safe_site_url = html.escape(PUBLIC_SITE_URL, quote=True)
    safe_email_lang = "zh-CN" if lang == "zh" else ("ms" if lang == "ms" else "en")
    safe_request_id = html.escape(hashlib.sha256(f"{safe_otp}:{safe_time}:{safe_ip}".encode("utf-8")).hexdigest()[:16])

    return f"""<!doctype html>
<html lang="{safe_email_lang}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,'Helvetica Neue',Helvetica,sans-serif;color:#000000;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f4f4;margin:0;padding:46px 12px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;max-width:760px;background:#ffffff;border-radius:0;border-collapse:collapse;">
                    <tr>
                        <td style="padding:54px 56px 0;">
                            <img src="cid:mof-logo" alt="MOF" width="42" style="display:block;width:42px;height:auto;filter:grayscale(100%);-webkit-filter:grayscale(100%);">
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:54px 56px 0;">
                            <h1 style="margin:0;font-size:30px;line-height:1.22;font-weight:700;letter-spacing:0;color:#000000;">{safe_title}</h1>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:48px 56px 0;">
                            <div style="font-size:44px;line-height:1;font-weight:700;letter-spacing:1px;color:#000000;font-variant-numeric:tabular-nums;">{safe_otp}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:56px 56px 0;">
                            <p style="margin:0;font-size:16px;line-height:1.85;color:#111111;">{safe_intro}</p>
                            <p style="margin:34px 0 0;font-size:16px;line-height:1.85;color:#111111;">{safe_validity}</p>
                            <p style="margin:34px 0 0;font-size:16px;line-height:1.85;color:#111111;">{safe_support}</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:52px 56px 0;">
                            <a href="{safe_site_url}" style="display:inline-block;background:#000000;color:#ffffff;text-decoration:none;border-radius:28px;padding:17px 34px;font-size:16px;line-height:1;font-weight:700;">{safe_button}</a>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:82px 56px 54px;">
                            <p style="margin:0;font-size:15px;line-height:1.6;color:#111111;">{safe_uid}: {safe_request_id}</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="border-top:1px solid #000000;padding:34px 56px 38px;">
                            <p style="margin:0;font-size:12px;line-height:1.7;color:#666666;">{safe_footer}</p>
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
) -> None:
    if not SMTP_APP_PASSWORD:
        raise RuntimeError("SMTP_APP_PASSWORD is not configured")

    lang = _normalize_language(language)
    copy = OTP_EMAIL_COPY[lang]
    msg = MIMEMultipart("related")
    msg["Subject"] = copy["subject"]
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_USERNAME))
    msg["To"] = to_email

    alternative = MIMEMultipart("alternative")
    text_body = (
        f"{copy['plain_code']}: {otp_code}\n"
        f"{copy['plain_validity']}\n\n"
        f"{copy['intro'].format(current_time=current_time, device_info=device_info, ip_address=ip_address)}\n\n"
        f"{copy['button']}: {PUBLIC_SITE_URL}"
    )
    alternative.attach(MIMEText(text_body, "plain", "utf-8"))
    alternative.attach(MIMEText(
        build_otp_email_html(otp_code, current_time, device_info, ip_address, lang),
        "html",
        "utf-8",
    ))
    msg.attach(alternative)

    logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "MOF_Logo.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as logo_file:
            logo = MIMEImage(logo_file.read())
        logo.add_header("Content-ID", "<mof-logo>")
        logo.add_header("Content-Disposition", "inline", filename="MOF_Logo.png")
        msg.attach(logo)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(SMTP_USERNAME, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_USERNAME, [to_email], msg.as_string())

def _store_pending_otp(
    pending_id: str,
    username: str,
    password_hash: str,
    otp_code: str,
    current_time: str,
    device_info: str,
    ip_address: str,
    language: Optional[str] = "en",
    theme: Optional[str] = "dark",
) -> None:
    _ensure_pending_otp_indexes()
    now = datetime.utcnow()
    pending_otps_col.delete_many({"username": username})
    pending_otps_col.insert_one({
        "_id": pending_id,
        "username": username,
        "display_name": generate_display_name(username),
        "preferences": {
            "language": _normalize_language(language),
            "theme": _normalize_theme(theme),
        },
        "password": password_hash,
        "otp": get_password_hash(otp_code),
        "created_at": now,
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
    _pending_otp_indexes_ready = True

# ─── Auth Endpoints ────────────────────────────────────

@auth_router.post("/api/register")
async def register(req: AuthRequest, request: Request):
    username = req.username.strip()
    language = _normalize_language(req.language)
    theme = _normalize_theme(req.theme)
    if not username or not req.password:
        raise HTTPException(status_code=400, detail="Username and password required")

    if "@" not in username:
        raise HTTPException(status_code=400, detail="Please use a valid email address for email registration")

    existing = users_col.find_one({"username": username})
    if existing and existing.get("status") == "active":
        raise HTTPException(status_code=400, detail="Username already taken")

    pending_id = str(uuid.uuid4())
    otp_code = generate_otp()
    current_time = _current_time_for_email()
    device_info = _device_info(request)
    ip_address = _client_ip(request)

    _store_pending_otp(
        pending_id=pending_id,
        username=username,
        password_hash=get_password_hash(req.password),
        otp_code=otp_code,
        current_time=current_time,
        device_info=device_info,
        ip_address=ip_address,
        language=language,
        theme=theme,
    )

    try:
        send_otp_email(username, otp_code, current_time, device_info, ip_address, language)
    except Exception as exc:
        pending_otps_col.delete_one({"_id": pending_id})
        raise HTTPException(status_code=502, detail=f"Failed to send OTP email: {exc}")

    if existing and existing.get("status") != "active":
        users_col.delete_one({"_id": existing["_id"]})

    return {"status": "pending_verification", "username": username, "user_id": pending_id}

@auth_router.post("/api/verify-otp")
async def verify_otp(req: VerifyOTPRequest):
    pending = pending_otps_col.find_one({"_id": req.user_id})
    if pending:
        if datetime.utcnow() > pending["expires_at"]:
            pending_otps_col.delete_one({"_id": req.user_id})
            raise HTTPException(status_code=400, detail="OTP has expired. Please register again to get a new code.")

        if not verify_password(req.otp, pending.get("otp", "")):
            raise HTTPException(status_code=400, detail="Invalid OTP code")

        username = pending["username"]
        existing = users_col.find_one({"username": username})
        if existing and existing.get("status") == "active":
            pending_otps_col.delete_one({"_id": req.user_id})
            raise HTTPException(status_code=400, detail="Username already taken")

        if existing:
            user_id = existing["_id"]
            users_col.update_one(
                {"_id": user_id},
                {"$set": {
                    "username": username,
                    "display_name": existing.get("display_name") or pending.get("display_name") or generate_display_name(username),
                    "password": pending["password"],
                    "preferences": pending.get("preferences") or _user_preferences(existing),
                    "status": "active",
                    "auth_provider": "local",
                    "verified_at": datetime.utcnow(),
                }, "$unset": {"otp": "", "otp_expiry": ""}}
            )
        else:
            user_id = str(uuid.uuid4())
            users_col.insert_one({
                "_id": user_id,
                "username": username,
                "display_name": pending.get("display_name") or generate_display_name(username),
                "preferences": pending.get("preferences") or {"language": "en", "theme": "dark"},
                "password": pending["password"],
                "status": "active",
                "auth_provider": "local",
                "created_at": datetime.utcnow(),
                "verified_at": datetime.utcnow(),
            })

        pending_otps_col.delete_one({"_id": req.user_id})
        token = create_jwt_token({"sub": str(user_id), "username": username})
        verified_user = users_col.find_one({"_id": user_id})

        return {
            "status": "success",
            "username": username,
            "display_name": _user_display_name(verified_user),
            "preferences": _user_preferences(verified_user),
            "user_id": str(user_id),
            "access_token": token
        }

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
    
    return {
        "status": "success", 
        "username": user.get("username"), 
        "display_name": _user_display_name(user),
        "preferences": _user_preferences(user),
        "user_id": req.user_id,
        "access_token": token
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

    otp_code = generate_otp()
    current_time = _current_time_for_email()
    device_info = _device_info(request)
    ip_address = _client_ip(request)
    language = _normalize_language(pending.get("language"))

    pending_otps_col.update_one(
        {"_id": req.user_id},
        {"$set": {
            "otp": get_password_hash(otp_code),
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES),
            "current_time": current_time,
            "device_info": device_info,
            "ip_address": ip_address,
            "language": language,
        }}
    )

    try:
        send_otp_email(username, otp_code, current_time, device_info, ip_address, language)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to send OTP email: {exc}")

    return {"status": "resent", "user_id": req.user_id}

@auth_router.post("/api/login")
async def login(req: AuthRequest):
    user = users_col.find_one({"username": req.username})
    
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

    token = create_jwt_token({"sub": str(user["_id"]), "username": user.get("username")})
        
    return {
        "status": "success", 
        "username": req.username, 
        "display_name": display_name,
        "preferences": _user_preferences(user),
        "user_id": str(user["_id"]),
        "access_token": token
    }

# ─── Third-Party OAuth Placeholders ────────────────────

import urllib.request
import json

@auth_router.post("/api/auth/google")
async def google_auth(req: OAuthRequest):
    # Retrieve User Info via Google Access Token
    try:
        url = "https://www.googleapis.com/oauth2/v3/userinfo"
        req_auth = urllib.request.Request(url, headers={"Authorization": f"Bearer {req.token}"})
        with urllib.request.urlopen(req_auth) as response:
            user_info = json.loads(response.read().decode())
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    google_sub = user_info.get("sub")
    email = user_info.get("email")
    name = user_info.get("name") or email.split('@')[0]
    picture = user_info.get("picture")
    preferences = {
        "language": _normalize_language(req.language),
        "theme": _normalize_theme(req.theme),
    }
    
    if not google_sub or not email:
        raise HTTPException(status_code=400, detail="Incomplete Google profile")

    # Associate or Create user in MongoDB
    user = users_col.find_one({"$or": [{"auth_provider_id": google_sub}, {"username": email}]})
    
    if not user:
        # Register new social user
        user_id = str(uuid.uuid4())
        users_col.insert_one({
            "_id": user_id,
            "username": email,
            "display_name": name,
            "preferences": preferences,
            "status": "active",
            "auth_provider": "google",
            "auth_provider_id": google_sub,
            "picture": picture,
            "created_at": datetime.utcnow()
        })
        token = create_jwt_token({"sub": user_id, "username": email})
        return {
            "status": "success", 
            "username": email, 
            "display_name": name,
            "preferences": preferences,
            "user_id": user_id,
            "avatarUrl": picture,
            "access_token": token
        }
    else:
        # User exists, optionally link google ID if log in via email previously
        updates = {}
        if "auth_provider_id" not in user:
            updates.update({"auth_provider": "google", "auth_provider_id": google_sub, "status": "active"})
        if picture and user.get("picture") != picture:
            updates.update({"picture": picture})
            user["picture"] = picture
        if not user.get("display_name") and name:
            updates.update({"display_name": name})
            user["display_name"] = name
        if not user.get("preferences"):
            updates.update({"preferences": preferences})
            user["preferences"] = preferences
            
        if updates:
            users_col.update_one({"_id": user["_id"]}, {"$set": updates})
        
        token = create_jwt_token({"sub": str(user["_id"]), "username": user.get("username")})
        return {
            "status": "success", 
            "username": user.get("username"), 
            "display_name": _user_display_name(user),
            "preferences": _user_preferences(user),
            "user_id": str(user["_id"]),
            "avatarUrl": user.get("picture"),
            "access_token": token
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
    if req.theme is not None:
        current["theme"] = _normalize_theme(req.theme)

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
    new_name = req.display_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Display name cannot be empty")
    if len(new_name) > 80:
        raise HTTPException(status_code=400, detail="Display name too long (max 80 chars)")
    users_col.update_one(
        {"_id": user["_id"]},
        {"$set": {"display_name": new_name, "updated_at": datetime.utcnow()}}
    )
    return {"status": "success", "display_name": new_name}

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
        raise HTTPException(status_code=502, detail=f"Failed to send OTP email: {exc}")

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
    lines.append("=== Ministry of Finance — Account Data Export ===")
    lines.append(f"Export Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("--- Personal Information ---")
    lines.append(f"Display Name : {user.get('display_name', '')}")
    lines.append(f"Email        : {email}")
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
    msg_out["Subject"] = "Your MOF Account Data Export"
    msg_out["From"] = formataddr((SMTP_FROM_NAME, SMTP_USERNAME))
    msg_out["To"] = email
    msg_out.attach(MIMEText("Please find your account data export attached.", "plain", "utf-8"))
    attachment = MIMEText(txt_content, "plain", "utf-8")
    attachment.add_header("Content-Disposition", "attachment", filename="mof_data_export.txt")
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
    google_email = user_info.get("email")
    picture = user_info.get("picture")

    if not google_sub or not google_email:
        raise HTTPException(status_code=400, detail="Incomplete Google profile")

    existing = users_col.find_one({"auth_provider_id": google_sub})
    if existing and str(existing["_id"]) != str(user["_id"]):
        raise HTTPException(status_code=409, detail="This Google account is already linked to another account")

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
    if not req.new_password or len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(req.new_password) > 200:
        raise HTTPException(status_code=400, detail="Password too long")
    hashed = get_password_hash(req.new_password)
    users_col.update_one(
        {"_id": user["_id"]},
        {"$set": {"password": hashed, "updated_at": datetime.utcnow()}}
    )
    return {"status": "success", "has_password": True}

@auth_router.post("/api/auth/apple")
async def apple_auth(req: OAuthRequest):
    # In production, verify the Apple JWT id_token structure.
    return {"status": "mocked", "detail": "Apple OAuth endpoint hit."}
