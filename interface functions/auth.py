import os
import hashlib
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
import uuid

# Connect to MongoDB
mongo_client = MongoClient("mongodb://localhost:27017/")
db = mongo_client["pepper_chat_db"]
users_col = db["users"]

# Create a FastAPI Router for auth endpoints
auth_router = APIRouter()

class AuthRequest(BaseModel):
    username: str
    password: str

def hash_password(password: str):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

@auth_router.post("/api/register")
async def register(req: AuthRequest):
    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="Username and password required")
        
    existing = users_col.find_one({"username": req.username})
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")
        
    user_id = str(uuid.uuid4())
    users_col.insert_one({
        "_id": user_id,
        "username": req.username,
        "password": hash_password(req.password)
    })
    return {"status": "success", "username": req.username, "user_id": user_id}

@auth_router.post("/api/login")
async def login(req: AuthRequest):
    user = users_col.find_one({
        "username": req.username, 
        "password": hash_password(req.password)
    })
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
        
    return {"status": "success", "username": req.username, "user_id": str(user["_id"])}
