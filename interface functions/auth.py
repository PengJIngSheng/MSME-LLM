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

class VerifyOTPRequest(BaseModel):
    user_id: str
    otp: str

class ResendOTPRequest(BaseModel):
    user_id: str

class OAuthRequest(BaseModel):
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

def build_otp_email_html(otp_code: str, current_time: str, device_info: str, ip_address: str) -> str:
    safe_otp = html.escape(otp_code)
    safe_time = html.escape(current_time)
    safe_device = html.escape(device_info)
    safe_ip = html.escape(ip_address)
    safe_site_url = html.escape(PUBLIC_SITE_URL, quote=True)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>邮箱注册验证码</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f8;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#111111;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f5f8;margin:0;padding:32px 16px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;max-width:640px;background:#ffffff;border:1px solid #eceef2;border-radius:12px;overflow:hidden;">
                    <tr>
                        <td style="padding:36px 42px 18px;">
                            <img src="cid:mof-logo" alt="MOF Logo" width="118" style="display:block;width:118px;height:auto;filter:grayscale(100%);-webkit-filter:grayscale(100%);">
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:10px 42px 8px;">
                            <h1 style="margin:0;font-size:28px;line-height:1.25;font-weight:700;letter-spacing:0;color:#050505;">邮箱注册验证码</h1>
                            <p style="margin:14px 0 0;font-size:15px;line-height:1.7;color:#555b66;">请使用下方 6 位数字验证码完成邮箱注册验证。</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:18px 42px 8px;">
                            <div style="display:inline-block;padding:18px 24px;border-radius:12px;background:#f6f7f9;border:1px solid #e9ebef;">
                                <div style="font-size:40px;line-height:1;font-weight:800;letter-spacing:8px;color:#000000;font-variant-numeric:tabular-nums;">{safe_otp}</div>
                            </div>
                            <p style="margin:16px 0 0;font-size:14px;line-height:1.7;color:#333333;">此验证码在 <strong>10 分钟</strong> 内有效。请勿向任何人泄露。</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:20px 42px 8px;">
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#fbfbfc;border:1px solid #eceef2;border-radius:8px;">
                                <tr>
                                    <td style="padding:16px 18px;border-bottom:1px solid #eceef2;font-size:13px;color:#6b7280;width:110px;">登录时间</td>
                                    <td style="padding:16px 18px;border-bottom:1px solid #eceef2;font-size:13px;color:#111111;">{safe_time}</td>
                                </tr>
                                <tr>
                                    <td style="padding:16px 18px;border-bottom:1px solid #eceef2;font-size:13px;color:#6b7280;width:110px;">使用设备</td>
                                    <td style="padding:16px 18px;border-bottom:1px solid #eceef2;font-size:13px;color:#111111;">{safe_device}</td>
                                </tr>
                                <tr>
                                    <td style="padding:16px 18px;font-size:13px;color:#6b7280;width:110px;">IP 地址</td>
                                    <td style="padding:16px 18px;font-size:13px;color:#111111;">{safe_ip}</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:24px 42px 38px;">
                            <p style="margin:0 0 22px;font-size:14px;line-height:1.7;color:#555b66;">如果这不是您本人发起的注册请求，可以忽略此邮件。</p>
                            <a href="{safe_site_url}" style="display:inline-block;background:#111111;color:#ffffff;text-decoration:none;border-radius:12px;padding:13px 22px;font-size:14px;font-weight:700;">查看官网</a>
                        </td>
                    </tr>
                </table>
                <div style="max-width:640px;margin:18px auto 0;text-align:center;font-size:12px;line-height:1.6;color:#8a8f98;">
                    Ministry of Finance 验证邮件。请勿回复此邮件。
                </div>
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
) -> None:
    if not SMTP_APP_PASSWORD:
        raise RuntimeError("SMTP_APP_PASSWORD is not configured")

    msg = MIMEMultipart("related")
    msg["Subject"] = "邮箱注册验证码"
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_USERNAME))
    msg["To"] = to_email

    alternative = MIMEMultipart("alternative")
    text_body = (
        f"邮箱注册验证码: {otp_code}\n"
        f"此验证码在 10 分钟内有效。\n\n"
        f"登录时间: {current_time}\n"
        f"使用设备: {device_info}\n"
        f"IP 地址: {ip_address}\n\n"
        f"查看官网: {PUBLIC_SITE_URL}"
    )
    alternative.attach(MIMEText(text_body, "plain", "utf-8"))
    alternative.attach(MIMEText(
        build_otp_email_html(otp_code, current_time, device_info, ip_address),
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
) -> None:
    _ensure_pending_otp_indexes()
    now = datetime.utcnow()
    pending_otps_col.delete_many({"username": username})
    pending_otps_col.insert_one({
        "_id": pending_id,
        "username": username,
        "password": password_hash,
        "otp": get_password_hash(otp_code),
        "created_at": now,
        "expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
        "current_time": current_time,
        "device_info": device_info,
        "ip_address": ip_address,
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
    )

    try:
        send_otp_email(username, otp_code, current_time, device_info, ip_address)
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
                    "password": pending["password"],
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
                "password": pending["password"],
                "status": "active",
                "auth_provider": "local",
                "created_at": datetime.utcnow(),
                "verified_at": datetime.utcnow(),
            })

        pending_otps_col.delete_one({"_id": req.user_id})
        token = create_jwt_token({"sub": str(user_id), "username": username})

        return {
            "status": "success",
            "username": username,
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

    pending_otps_col.update_one(
        {"_id": req.user_id},
        {"$set": {
            "otp": get_password_hash(otp_code),
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES),
            "current_time": current_time,
            "device_info": device_info,
            "ip_address": ip_address,
        }}
    )

    try:
        send_otp_email(username, otp_code, current_time, device_info, ip_address)
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
        
    token = create_jwt_token({"sub": str(user["_id"]), "username": user.get("username")})
        
    return {
        "status": "success", 
        "username": req.username, 
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
            
        if updates:
            users_col.update_one({"_id": user["_id"]}, {"$set": updates})
        
        token = create_jwt_token({"sub": str(user["_id"]), "username": user.get("username")})
        return {
            "status": "success", 
            "username": user.get("username"), 
            "user_id": str(user["_id"]),
            "avatarUrl": user.get("picture"),
            "access_token": token
        }

@auth_router.post("/api/auth/apple")
async def apple_auth(req: OAuthRequest):
    # In production, verify the Apple JWT id_token structure.
    return {"status": "mocked", "detail": "Apple OAuth endpoint hit."}
