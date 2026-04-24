import os
import uuid
import random
import hashlib
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException
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

auth_router = APIRouter()

# ─── Pydantic Models ───────────────────────────────────
class AuthRequest(BaseModel):
    username: str
    password: str

class VerifyOTPRequest(BaseModel):
    user_id: str
    otp: str

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
    return str(random.randint(100000, 999999))

# ─── Auth Endpoints ────────────────────────────────────

@auth_router.post("/api/register")
async def register(req: AuthRequest):
    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="Username and password required")
        
    existing = users_col.find_one({"username": req.username})
    
    otp_code = generate_otp()
    otp_hash = get_password_hash(otp_code)
    otp_expiry = datetime.utcnow() + timedelta(minutes=10)
    
    # 🚨 SECURITY/DEV NOTE: Print OTP to console since SMTP is not yet configured 🚨
    print(f"\n========================================================")
    print(f"📧 [DEV EMAIL INTERCEPT] OTP for {req.username}: {otp_code}")
    print(f"========================================================\n")
    
    if existing:
        if existing.get("status") == "active":
            raise HTTPException(status_code=400, detail="Username already taken")
        else:
            # User is unverified, regenerating OTP
            user_id = existing["_id"]
            users_col.update_one(
                {"_id": user_id},
                {"$set": {
                    "password": get_password_hash(req.password),
                    "otp": otp_hash,
                    "otp_expiry": otp_expiry
                }}
            )
    else:
        user_id = str(uuid.uuid4())
        users_col.insert_one({
            "_id": user_id,
            "username": req.username,
            "password": get_password_hash(req.password),
            "status": "unverified",
            "otp": otp_hash,
            "otp_expiry": otp_expiry,
            "auth_provider": "local",
            "created_at": datetime.utcnow()
        })
        
    return {"status": "pending_verification", "username": req.username, "user_id": user_id}

@auth_router.post("/api/verify-otp")
async def verify_otp(req: VerifyOTPRequest):
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
