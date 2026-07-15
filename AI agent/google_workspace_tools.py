import os
import sys
import datetime
import json
import base64
import logging
import html
import re
import uuid
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Depends, Header, Cookie
from pydantic import BaseModel
from pymongo import MongoClient
import jwt
from cryptography.fernet import Fernet, InvalidToken

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config_loader import cfg

connectors_router = APIRouter()
logger = logging.getLogger(__name__)

# MongoDB setup
mongo_client = MongoClient(cfg.mongo_uri)
db = mongo_client[cfg.mongo_database]
users_col = db["users"]

JWT_SECRET = cfg.jwt_secret
JWT_ALGORITHM = cfg.jwt_algorithm
GOOGLE_OAUTH_CLIENT_ID = cfg.google_oauth_client_id
GOOGLE_OAUTH_CLIENT_SECRET = cfg.google_oauth_client_secret.strip()

# -- Note: This file path will need to be downloaded from Google Cloud Console.
CLIENT_SECRETS_FILE = cfg.google_client_secret_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_secret.json")

SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.metadata.readonly',
    'https://www.googleapis.com/auth/presentations'
]

SERVICE_SCOPES = {
    "drive": ['https://www.googleapis.com/auth/drive.file'],
    "gmail": ['https://www.googleapis.com/auth/gmail.send'],
    "docs": ['https://www.googleapis.com/auth/documents'],
    "sheets": [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.metadata.readonly',
    ],
    "slides": [
        'https://www.googleapis.com/auth/presentations',
        'https://www.googleapis.com/auth/drive.file',
    ],
    "calendar": ['https://www.googleapis.com/auth/calendar.events'],
    "meet": ['https://www.googleapis.com/auth/calendar.events'],
}

def _scopes_for_service(service_id: str) -> list:
    return SERVICE_SCOPES.get((service_id or "").strip().lower(), SCOPES)


def _token_cipher() -> Fernet:
    key = cfg.google_token_encryption_key
    if not key:
        raise PermissionError(
            "Google Workspace is unavailable until GOOGLE_TOKEN_ENCRYPTION_KEY is configured."
        )
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise PermissionError("GOOGLE_TOKEN_ENCRYPTION_KEY is invalid.") from exc


def _encrypt_token(value: str | None) -> str:
    if not value:
        return ""
    return "enc:v1:" + _token_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt_token(value: str | None) -> str:
    encoded = str(value or "")
    if not encoded:
        return ""
    if not encoded.startswith("enc:v1:"):
        raise PermissionError("Reconnect this Google Workspace service to secure its credentials.")
    try:
        return _token_cipher().decrypt(encoded.removeprefix("enc:v1:").encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise PermissionError("Google Workspace credentials cannot be decrypted. Reconnect the service.") from exc

def _parse_google_token_expiry(value):
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None

def _email_body_to_html(body: str, subject: str = "") -> str:
    """Build a clean HTML alternative while keeping the plain-text body intact."""
    safe_subject = html.escape(subject or "MSME.AI")
    safe_body = html.escape(body or "").replace("\n", "<br>")
    return f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#111;">
  <div style="max-width:680px;margin:0 auto;padding:32px 18px;">
    <div style="background:#fff;border:1px solid #e7e7e7;border-radius:12px;overflow:hidden;">
      <div style="padding:24px 28px;border-bottom:1px solid #eee;">
        <div style="font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:#6b7280;font-weight:700;">MSME.AI</div>
        <h1 style="margin:10px 0 0;font-size:22px;line-height:1.3;color:#111;">{safe_subject}</h1>
      </div>
      <div style="padding:28px;font-size:15px;line-height:1.75;color:#202124;">
        {safe_body}
      </div>
      <div style="padding:18px 28px;border-top:1px solid #eee;color:#8a8f98;font-size:12px;">
        Sent via MSME.AI Workspace Agent
      </div>
    </div>
  </div>
</body>
</html>"""

def _origin(url: str) -> str:
    parsed = urlparse(url or "")
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"

def _allowed_redirect_origins() -> list:
    origins = {
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        _origin(cfg.public_site_url),
    }
    return sorted(origin for origin in origins if origin)

def _google_oauth_client_config() -> dict:
    """Return the OAuth web-client config that must match GIS initCodeClient."""
    redirect_origins = _allowed_redirect_origins()
    if GOOGLE_OAUTH_CLIENT_SECRET:
        return {
            "web": {
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": redirect_origins,
                "javascript_origins": redirect_origins,
            }
        }

    if not CLIENT_SECRETS_FILE or not os.path.exists(CLIENT_SECRETS_FILE):
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing Google OAuth client secret. Set GOOGLE_OAUTH_CLIENT_SECRET "
                "or GOOGLE_CLIENT_SECRET_FILE for the active APP_PROFILE."
            )
        )

    with open(CLIENT_SECRETS_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    section_name = "web" if "web" in config else "installed"
    section = config.get(section_name) or {}
    configured_client_id = section.get("client_id")

    if configured_client_id != GOOGLE_OAUTH_CLIENT_ID:
        raise HTTPException(
            status_code=400,
            detail=(
                "Google OAuth client mismatch. Frontend uses "
                f"{GOOGLE_OAUTH_CLIENT_ID}, but backend client_secret.json uses "
                f"{configured_client_id or 'none'}. Replace AI agent/client_secret.json "
                "with the matching Web OAuth client JSON, or set GOOGLE_OAUTH_CLIENT_SECRET "
                "for that client id."
            )
        )

    if not section.get("client_secret"):
        raise HTTPException(
            status_code=400,
            detail="Missing Google OAuth client secret for Workspace connector authorization."
        )

    return config


def _application_client_secret() -> str:
    if GOOGLE_OAUTH_CLIENT_SECRET:
        return GOOGLE_OAUTH_CLIENT_SECRET
    config = _google_oauth_client_config()
    section = config.get("web") or config.get("installed") or {}
    secret = str(section.get("client_secret") or "")
    if not secret:
        raise PermissionError("Google OAuth client secret is not configured.")
    return secret

class AuthCodeRequest(BaseModel):
    auth_code: str
    redirect_uri: str = ""
    service_id: str = None

class ConnectorToggleRequest(BaseModel):
    service: str
    enabled: bool

def get_current_user(authorization: str = Header(None), msme_session: str = Cookie(None)):
    candidates = []
    if authorization and authorization.startswith("Bearer "):
        candidates.append(authorization.split(" ", 1)[1])
    if msme_session and msme_session not in candidates:
        candidates.append(msme_session)
    if not candidates:
        raise HTTPException(status_code=401, detail="Unauthorized")
    expired = False
    for token in candidates:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            expired = True
            continue
        except jwt.PyJWTError:
            continue
        user_id = payload.get("sub")
        if user_id:
            return user_id
    raise HTTPException(status_code=401, detail="Token expired" if expired else "Invalid token")

@connectors_router.post("/api/connectors/exchange_code")
async def exchange_auth_code(req: AuthCodeRequest, user_id: str = Depends(get_current_user)):
    """Exchange offline auth code for a refresh token and access token"""
    try:
        redirect_uri = _origin(req.redirect_uri) or _origin(cfg.public_site_url)
        if redirect_uri not in _allowed_redirect_origins():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Google OAuth redirect origin is not allowed by this backend profile. "
                    f"Received {redirect_uri or 'empty'}; expected one of "
                    f"{', '.join(_allowed_redirect_origins())}."
                )
            )
        service_id = req.service_id or "default"
        service_scopes = _scopes_for_service(service_id)
        flow = Flow.from_client_config(
            _google_oauth_client_config(), scopes=service_scopes, redirect_uri=redirect_uri
        )
        flow.fetch_token(code=req.auth_code)
        credentials = flow.credentials
        
        creds_data = {
            "google_token": _encrypt_token(credentials.token),
            "google_refresh_token": _encrypt_token(credentials.refresh_token),
            "google_token_expiry": credentials.expiry.isoformat() if credentials.expiry else None,
            "google_scopes": service_scopes,
            "google_token_updated_at": datetime.datetime.utcnow()
        }
        
        users_col.update_one(
            {"_id": user_id},
            {"$set": {f"google_creds_{service_id}": creds_data}}
        )
        return {"status": "success", "message": "Google Workspace connected with offline access."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Google connector token exchange failed")
        msg = str(e)
        if "redirect_uri_mismatch" in msg or "redirect_uri" in msg:
            detail = (
                "Google OAuth redirect URI mismatch. Add the exact current site origin "
                "to the OAuth client's Authorized JavaScript origins and Authorized "
                "redirect URIs."
            )
        elif "invalid_client" in msg or "unauthorized_client" in msg:
            detail = (
                "Google OAuth client is not authorized for this site. Check the client id, "
                "client secret, Authorized JavaScript origins, and OAuth consent screen."
            )
        else:
            detail = "Google Workspace authorization failed. Please check the OAuth client configuration."
        raise HTTPException(status_code=400, detail=detail)

@connectors_router.post("/api/connectors/toggle")
async def toggle_connector(req: ConnectorToggleRequest, user_id: str = Depends(get_current_user)):
    user = users_col.find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if not req.enabled:
        users_col.update_one({"_id": user_id}, {"$unset": {f"google_creds_{req.service}": ""}})
        
    return {"status": "success"}

@connectors_router.post("/api/connectors/clear_all")
async def clear_all_connectors(user_id: str = Depends(get_current_user)):
    """Wipe ALL Google credentials so user must re-authorize every session."""
    users_col.update_one(
        {"_id": user_id},
        {"$unset": {
            "google_creds_drive": "",
            "google_creds_gmail": "",
            "google_creds_docs": "",
            "google_creds_calendar": "",
            "google_creds_meet": "",
            "google_creds_sheets": "",
            "google_creds_slides": "",
            # Legacy fields
            "google_token": "",
            "google_refresh_token": "",
            "google_scopes": "",
            "google_token_expiry": "",
            "google_token_uri": "",
            "google_client_id": "",
            "google_client_secret": "",
            "google_token_updated_at": "",
        }}
    )
    return {"status": "success"}

@connectors_router.get("/api/connectors/status")
async def get_status(user_id: str = Depends(get_current_user)):
    user = users_col.find_one({"_id": user_id})

    scope_markers = {
        "drive": "drive.file",
        "gmail": "gmail.send",
        "docs": "documents",
        "calendar": "calendar.events",
        "meet": "calendar.events",
        "sheets": ["spreadsheets", "drive.metadata.readonly"],
        "slides": ["presentations", "drive.file"],
    }
    status_map = {service: {"granted": False, "active": False} for service in scope_markers}

    if not user:
        return status_map
    if not user.get("auth_provider_id"):
        return status_map

    for service, marker in scope_markers.items():
        creds = user.get(f"google_creds_{service}") or {}
        scopes = creds.get("google_scopes", [])
        scopes_str = " ".join(scopes) if isinstance(scopes, list) else str(scopes)
        has_token = bool(
            str(creds.get("google_refresh_token") or "").startswith("enc:v1:")
            or str(creds.get("google_token") or "").startswith("enc:v1:")
        )
        markers = marker if isinstance(marker, list) else [marker]
        active = has_token and all(item in scopes_str for item in markers)
        status_map[service]["granted"] = active
        status_map[service]["active"] = active

    return status_map

def get_google_creds_offline(user_id: str, service_id: str = "default") -> Credentials:
    """Retrieve credentials with automatic refresh mechanism"""
    user = users_col.find_one({"_id": user_id})
    if not user:
        raise ValueError("User not found")
    if not user.get("auth_provider_id"):
        raise ValueError("Google account is not linked")

    creds_dict = user.get(f"google_creds_{service_id}")
    if not creds_dict or not creds_dict.get("google_refresh_token"):
        raise ValueError(f"Google Workspace {service_id} not authorized")

    service_scopes = _scopes_for_service(service_id)
    creds = Credentials(
        token=_decrypt_token(creds_dict.get("google_token")),
        refresh_token=_decrypt_token(creds_dict.get("google_refresh_token")),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_OAUTH_CLIENT_ID,
        client_secret=_application_client_secret(),
        scopes=service_scopes,
        expiry=_parse_google_token_expiry(creds_dict.get("google_token_expiry"))
    )

    if creds_dict.get("google_scopes") != service_scopes:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {f"google_creds_{service_id}.google_scopes": service_scopes}}
        )
    
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        users_col.update_one(
            {"_id": user_id}, 
            {"$set": {
                f"google_creds_{service_id}.google_token": _encrypt_token(creds.token),
                f"google_creds_{service_id}.google_token_expiry": creds.expiry.isoformat() if creds.expiry else None,
                f"google_creds_{service_id}.google_scopes": service_scopes,
            }}
        )
        
    return creds

# --- Tool Execution Functions ---

def tool_drive_upload(user_id: str, file_path: str, file_name: str, lang: str = "zh") -> str:
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    try:
        creds = get_google_creds_offline(user_id, "drive")
        
        required_scope = 'https://www.googleapis.com/auth/drive.file'
        if not creds.scopes or required_scope not in creds.scopes:
            if lang == "en":
                return "⚠️ Operation blocked: You are missing Google Drive access permissions. 👉 Please enable the switch in the sidebar."
            elif lang == "ms":
                return "⚠️ Operasi disekat: Anda kehilangan kebenaran akses Google Drive. 👉 Sila hidupkan suis di bar sisi."
            return "⚠️ 操作被拦截：您未授权 Google Drive 访问权限。👉 请重新打开侧边栏开关授权。"

        service = build('drive', 'v3', credentials=creds)
        file_metadata = {'name': file_name}
        media = MediaFileUpload(file_path, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        
        link = file.get('webViewLink')
        if lang == "en":
            return f"✅ File successfully uploaded to Google Drive: {link}"
        elif lang == "ms":
            return f"✅ Fail berjaya dimuat naik ke Google Drive: {link}"
        return f"✅ 文件已成功上传至 Google Drive: {link}"
        
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_drive": ""}})
            if lang == "en":
                return "⚠️ File upload failed: API permission denied. Sidebar switch has been reset."
            elif lang == "ms":
                return "⚠️ Muat naik fail gagal: Kebenaran API ditolak. Suis bar sisi telah ditetapkan semula."
            return "⚠️ 操作失败：API 权限验证未通过（Token 过期或未授权）。侧边栏开关已被重置，请重新开启以获取完整授权。"
            
        err_reason = getattr(error, 'reason', str(error))
        if lang == "en":
            return f"⚠️ File upload failed. Reason: {err_reason}"
        elif lang == "ms":
            return f"⚠️ Muat naik fail gagal. Sebab: {err_reason}"
        return f"⚠️ 文件上传失败，原因：{err_reason}"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Error: {str(e)}"
        elif lang == "ms":
            return f"⚠️ Ralat: {str(e)}"
        return f"⚠️ 发生错误：{str(e)}"

def tool_gmail_send(user_id: str, to: str, subject: str, body: str, attachment_path: str = None, lang: str = "zh") -> str:
    from googleapiclient.errors import HttpError
    try:
        creds = get_google_creds_offline(user_id, "gmail")
        
        # 1. Pre-flight Auth Check
        required_scope = 'https://www.googleapis.com/auth/gmail.send'
        if not creds.scopes or required_scope not in creds.scopes:
            if lang == "en":
                return (
                    "⚠️ Email send blocked: You are missing Gmail send permissions.\n\n"
                    "👉 *Action required: Please enable the Gmail switch in the Connectors sidebar.*"
                )
            elif lang == "ms":
                return (
                    "⚠️ Penghantaran e-mel disekat: Anda kehilangan kebenaran hantar Gmail.\n\n"
                    "👉 *Tindakan diperlukan: Sila hidupkan suis Gmail di bar sisi Connectors.*"
                )
            return (
                "⚠️ 邮件发送被拦截：您缺少 Gmail 的发送权限。\n\n"
                "👉 *操作指引：请在左侧边栏的 Connectors 中开启 Gmail 开关以授权。*"
            )

        # 2. Check if body was polluted by previous errors
        if "HttpError" in body or "Insufficient Permission" in body or "⚠️ 工具执行异常" in body:
            if lang == "en":
                return "⚠️ Send interrupted: System detected the content contains a previous error message. We prevented this abnormal send. Please tell me again what to send."
            elif lang == "ms":
                return "⚠️ Penghantaran diganggu: Sistem mengesan kandungan mempunyai mesej ralat sebelumnya. Kami menghalang penghantaran ini. Sila beritahu saya kembali apa yang perlu dihantar."
            return "⚠️ 发送中断：系统检测到要发送的内容是之前的系统报错，已为您阻止此次异常发送。请重新告知我需要发送哪些内容。"

        service = build('gmail', 'v1', credentials=creds)
        
        # Build email with optional attachment
        if attachment_path and os.path.exists(attachment_path):
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from email.mime.base import MIMEBase
            from email import encoders
            
            msg = MIMEMultipart('mixed')
            msg['To'] = to
            msg['Subject'] = subject if subject else "Pepper Chat Analysis Report"
            alt = MIMEMultipart('alternative')
            alt.attach(MIMEText(body, 'plain', 'utf-8'))
            alt.attach(MIMEText(_email_body_to_html(body, msg['Subject']), 'html', 'utf-8'))
            msg.attach(alt)
            
            with open(attachment_path, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename="{os.path.basename(attachment_path)}"'
                )
                msg.attach(part)
            
            encoded_message = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        else:
            final_subject = subject if subject else "Pepper Chat Analysis Report"
            message = EmailMessage()
            message.set_content(body)
            message.add_alternative(_email_body_to_html(body, final_subject), subtype='html')
            message['To'] = to
            message['Subject'] = final_subject
            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        create_message = {'raw': encoded_message}
        send_message = service.users().messages().send(userId="me", body=create_message).execute()
        
        if lang == "en":
            attach_note = f" (Attachment: {os.path.basename(attachment_path)})" if attachment_path else ""
            return f"✅ Email successfully sent to {to}!{attach_note}"
        elif lang == "ms":
            attach_note = f" (Lampiran: {os.path.basename(attachment_path)})" if attachment_path else ""
            return f"✅ E-mel berjaya dihantar ke {to}!{attach_note}"
        else:
            attach_note = f" (附件: {os.path.basename(attachment_path)})" if attachment_path else ""
            return f"✅ 邮件已成功发送到 {to}！{attach_note}"
        
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_gmail": ""}})
            if lang == "en":
                return "⚠️ Email failed to send: API permission denied (Token expired or incomplete). Sidebar switch has been reset, please re-enable it."
            elif lang == "ms":
                return "⚠️ E-mel gagal dihantar: Kebenaran API ditolak (Token tamat tempoh). Suis bar sisi telah ditetapkan semula, sila aktifkan semula."
            return "⚠️ 邮件发送失败：API 权限验证未通过（Token过期或未被完整授权）。侧边栏授权开关已被重置，请重新开启开关。"
            
        err_reason = getattr(error, 'reason', str(error))
        if lang == "en":
            return f"⚠️ Email failed to send. Reason: {err_reason}. Try again?"
        elif lang == "ms":
            return f"⚠️ E-mel gagal dihantar. Sebab: {err_reason}. Cuba lagi?"
        return f"⚠️ 邮件发送失败，原因：{err_reason}。要重试吗？"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Unexpected error: {str(e)}. Cannot complete sending."
        elif lang == "ms":
            return f"⚠️ Ralat tidak dijangka: {str(e)}. Tidak dapat menyelesaikan penghantaran."
        return f"⚠️ 发生意外错误：{str(e)}。无法完成发送。"

def _strip_markdown_for_docs(text: str) -> str:
    """Clean markdown artifacts that shouldn't appear in a Google Doc."""
    import re as _re
    # Remove <think>...</think> and unclosed <think>
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
    text = _re.sub(r"<think>.*", "", text, flags=_re.DOTALL)
    # Remove code fences
    text = _re.sub(r"```[\s\S]*?```", "", text)
    # Remove horizontal rules (--- or ***)
    text = _re.sub(r"^-{3,}$", "", text, flags=_re.MULTILINE)
    text = _re.sub(r"^\*{3,}$", "", text, flags=_re.MULTILINE)
    # Remove image links ![...](...)
    text = _re.sub(r"!\[.*?\]\(.*?\)", "", text)
    # Remove link syntax but keep text [text](url) -> text
    text = _re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    # Collapse multiple blank lines
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_markdown_tables(lines: list) -> list:
    """
    Parse lines into blocks: either ('text', [lines]) or ('table', [[row_cells], ...]).
    Separates table blocks from regular text blocks. Robust to missing outer pipes.
    """
    import re as _re
    blocks = []
    current_text = []
    current_table = []
    
    for curr_idx, line in enumerate(lines):
        stripped = line.strip()
        
        # A line is an explicit separator if it's mostly dashes and pipes.
        is_separator = bool(_re.match(r"^\|?[\s\-:|]+\|?$", stripped)) and stripped.count('-') >= 2
        
        # A line could be a table row if it has at least one pipe.
        has_pipe = stripped.count('|') >= 1
        
        # We consider it a table row if:
        # 1. We are already inside a table and it has a pipe.
        # 2. It has a pipe AND the NEXT line is a separator (we look ahead).
        # 3. It's a separator itself.
        # 4. It has >= 2 pipes (very likely a table).
        is_table_row = False
        if is_separator or (has_pipe and current_table) or stripped.count('|') >= 2:
            is_table_row = True
        elif has_pipe:
            # Look ahead for a separator
            if curr_idx + 1 < len(lines):
                next_stripped = lines[curr_idx + 1].strip()
                if bool(_re.match(r"^\|?[\s\-:|]+\|?$", next_stripped)) and next_stripped.count('-') >= 2:
                    is_table_row = True
        
        if is_table_row:
            # Flush text block if any
            if current_text:
                blocks.append(("text", current_text))
                current_text = []
                
            if not is_separator:
                # Parse cells safely: remove trailing/leading pipes if they exist, then split
                s = stripped
                if s.startswith('|'): s = s[1:]
                if s.endswith('|'): s = s[:-1]
                cells = [c.strip() for c in s.split("|")]
                current_table.append(cells)
        else:
            # Flush table block if any
            if current_table:
                # Only keep tables with at least 1 actual data row
                if len(current_table) > 0:
                    blocks.append(("table", current_table))
                current_table = []
            current_text.append(line)
    
    # Flush remaining
    if current_table:
        blocks.append(("table", current_table))
    if current_text:
        blocks.append(("text", current_text))
    
    return blocks


def _clean_bold_text(text: str) -> str:
    """Remove ** markers from text."""
    import re as _re
    text = _re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = _re.sub(r"(?<!\*)\*(?!\*)", "", text)
    return text


def _parse_markdown_to_doc_requests(text: str):
    """
    Parse markdown text and return (plain_text, formatting_requests, table_data).
    
    - plain_text: the text content to insert (tables replaced with placeholder newline)
    - formatting_requests: list of Google Docs API requests for headings, bold, bullets
    - table_data: list of (insert_index, rows_data) for table insertion
    """
    import re as _re
    
    text = _strip_markdown_for_docs(text)
    lines = text.split("\n")
    blocks = _parse_markdown_tables(lines)
    
    # Build plain text and record formatting
    plain_lines = []
    format_specs = []  # (line_index, type, data)
    bold_source = []   # original lines (before bold stripping) for bold detection
    table_inserts = [] # (line_index, table_rows) — placeholder line indices
    
    for block_type, block_data in blocks:
        if block_type == "table":
            # Insert a placeholder line for the table (will be replaced later)
            # Record which line index this table should go at
            table_inserts.append((len(plain_lines), block_data))
            # Add empty placeholder line (will be removed when table is inserted)
            plain_lines.append("")
            bold_source.append("")
            continue
        
        for line in block_data:
            stripped = line.strip()
            
            # Detect headings: # through ######
            heading_match = _re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if heading_match:
                level = min(len(heading_match.group(1)), 6)
                clean_text = _clean_bold_text(heading_match.group(2).strip())
                plain_lines.append(clean_text)
                bold_source.append(heading_match.group(2).strip())
                format_specs.append((len(plain_lines) - 1, "heading", level))
                continue
            
            # Detect bullet lists: - item or * item (but not ** bold)
            bullet_match = _re.match(r"^[-]\s+(.+)$", stripped)
            if not bullet_match:
                bullet_match = _re.match(r"^\*\s+([^*].*)$", stripped)
            if bullet_match:
                raw_text = bullet_match.group(1).strip()
                clean_text = _clean_bold_text(raw_text)
                plain_lines.append(clean_text)
                bold_source.append(raw_text)
                format_specs.append((len(plain_lines) - 1, "bullet", None))
                continue
            
            # Detect numbered lists: 1. item or 1) item
            num_match = _re.match(r"^\d+[.\)]\s+(.+)$", stripped)
            if num_match:
                raw_text = num_match.group(1).strip()
                clean_text = _clean_bold_text(raw_text)
                plain_lines.append(clean_text)
                bold_source.append(raw_text)
                format_specs.append((len(plain_lines) - 1, "numbered", None))
                continue
            
            # Regular line
            raw_text = stripped
            clean_text = _clean_bold_text(raw_text)
            plain_lines.append(clean_text)
            bold_source.append(raw_text)
    
    # Join into document text
    full_text = "\n".join(plain_lines)
    if not full_text.endswith("\n"):
        full_text += "\n"
    
    # Calculate line offsets (Google Docs index starts at 1)
    requests = []
    offset = 1
    line_offsets = []
    for line in plain_lines:
        line_offsets.append(offset)
        offset += len(line) + 1
    
    # Apply heading styles
    for line_idx, fmt_type, fmt_data in format_specs:
        start = line_offsets[line_idx]
        end = start + len(plain_lines[line_idx])
        
        if fmt_type == "heading":
            heading_map = {
                1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3",
                4: "HEADING_4", 5: "HEADING_5", 6: "HEADING_6"
            }
            named_style = heading_map.get(fmt_data, "HEADING_3")
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": start, "endIndex": end + 1},
                    "paragraphStyle": {"namedStyleType": named_style},
                    "fields": "namedStyleType"
                }
            })
        elif fmt_type == "bullet":
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": start, "endIndex": end + 1},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
                }
            })
        elif fmt_type == "numbered":
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": start, "endIndex": end + 1},
                    "bulletPreset": "NUMBERED_DECIMAL_NESTED"
                }
            })
    
    # Bold detection using original (pre-stripped) lines
    clean_offset = 1
    for i, raw_line in enumerate(bold_source):
        if i >= len(plain_lines):
            break
        # Find **bold** spans in original text
        pos = 0
        clean_pos = clean_offset
        for bold_match in _re.finditer(r"\*\*(.+?)\*\*", raw_line):
            pre_text = raw_line[pos:bold_match.start()]
            pre_clean = _clean_bold_text(pre_text)
            clean_pos += len(pre_clean)
            
            bold_text = bold_match.group(1)
            bold_start = clean_pos
            bold_end = bold_start + len(bold_text)
            
            if bold_start < bold_end and bold_start >= 1:
                requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": bold_start, "endIndex": bold_end},
                        "textStyle": {"bold": True},
                        "fields": "bold"
                    }
                })
            clean_pos = bold_end
            pos = bold_match.end()
        
        clean_offset += len(plain_lines[i]) + 1
    
    # Prepare table data with their insertion indices
    table_data = []
    for line_idx, rows in table_inserts:
        insert_idx = line_offsets[line_idx] if line_idx < len(line_offsets) else offset
        table_data.append((insert_idx, rows))
    
    return full_text, requests, table_data


def tool_docs_create(user_id: str, title: str, content: str, lang: str = "zh") -> str:
    from googleapiclient.errors import HttpError
    try:
        creds = get_google_creds_offline(user_id, "docs")
        required_scope = 'https://www.googleapis.com/auth/documents'
        if not creds.scopes or required_scope not in creds.scopes:
            if lang == "en":
                return "⚠️ Operation blocked: You are missing Google Docs access permissions. 👉 Please enable the switch in the sidebar to authorize."
            elif lang == "ms":
                return "⚠️ Operasi disekat: Anda kehilangan kebenaran akses Google Docs. 👉 Sila hidupkan suis di bar sisi untuk memberi kebenaran."
            return "⚠️ 操作被拦截：您缺少 Google Docs 访问权限。👉 请在侧边栏中开启开关以授权。"
            
        service = build('docs', 'v1', credentials=creds)
        document = service.documents().create(body={'title': title}).execute()
        doc_id = document.get('documentId')
        
        # Parse markdown into plain text + formatting + tables
        plain_text, format_requests, table_data = _parse_markdown_to_doc_requests(content)
        
        # Step 1: Insert the plain text + apply formatting
        all_requests = [{
            'insertText': {
                'location': {'index': 1},
                'text': plain_text
            }
        }]
        all_requests.extend(format_requests)
        
        service.documents().batchUpdate(
            documentId=doc_id, 
            body={'requests': all_requests}
        ).execute()
        
        # Step 2: Insert tables (if any) — inline at proper positions
        if table_data:
            try:
                # Filter valid tables
                valid_table_data = [t for t in table_data if t[1] and len(t[1]) >= 1]
                
                # 2.1 Insert all empty tables from back to front
                # Executing backwards preserves the insertion indices for earlier tables
                table_requests = []
                for insert_idx, rows in reversed(valid_table_data):
                    num_cols = max(len(r) for r in rows)
                    num_rows = len(rows)
                    table_requests.append({
                        'insertTable': {
                            'rows': num_rows,
                            'columns': num_cols,
                            'location': {'index': insert_idx}
                        }
                    })
                
                if table_requests:
                    service.documents().batchUpdate(
                        documentId=doc_id,
                        body={'requests': table_requests}
                    ).execute()
                
                # 2.2 Fetch document to get table IDs/structures
                doc = service.documents().get(documentId=doc_id).execute()
                doc_body = doc.get('body', {}).get('content', [])
                
                # Extract all tables from the document body in order
                doc_tables = [element['table'] for element in doc_body if 'table' in element]
                
                cell_requests = []
                # We map the sequentially fetched structural tables to our valid_table_data
                for t_idx, (insert_idx, rows) in enumerate(valid_table_data):
                    if t_idx >= len(doc_tables):
                        break
                    table = doc_tables[t_idx]
                    
                    for r_idx, table_row in enumerate(table.get('tableRows', [])):
                        for c_idx, table_cell in enumerate(table_row.get('tableCells', [])):
                            if r_idx < len(rows) and c_idx < len(rows[r_idx]):
                                cell_text = _clean_bold_text(rows[r_idx][c_idx])
                                # Also strip heading hashes just in case
                                cell_text = cell_text.lstrip("#").strip()
                                
                                cell_content = table_cell.get('content', [])
                                if cell_content:
                                    para = cell_content[0]
                                    start_idx = para.get('startIndex', 0)
                                    if cell_text:
                                        cell_requests.append({
                                            'insertText': {
                                                'location': {'index': start_idx},
                                                'text': cell_text
                                            }
                                        })
                
                # Sort all cell text insertions DESCENDING so indices don't shift
                cell_requests.sort(
                    key=lambda r: r['insertText']['location']['index'],
                    reverse=True
                )
                
                if cell_requests:
                    service.documents().batchUpdate(
                        documentId=doc_id,
                        body={'requests': cell_requests}
                    ).execute()
                
                # 2.3 Apply Bold to headers
                # After inserting all cell texts, indices shifted. Re-fetch doc.
                doc = service.documents().get(documentId=doc_id).execute()
                doc_body = doc.get('body', {}).get('content', [])
                header_bold_reqs = []
                for element in doc_body:
                    if 'table' in element:
                        first_row = element['table'].get('tableRows', [{}])[0]
                        for cell in first_row.get('tableCells', []):
                            cell_content = cell.get('content', [])
                            if cell_content:
                                para = cell_content[0]
                                s = para.get('startIndex', 0)
                                e = para.get('endIndex', s)
                                if e > s + 1:
                                    header_bold_reqs.append({
                                        'updateTextStyle': {
                                            'range': {'startIndex': s, 'endIndex': e - 1},
                                            'textStyle': {'bold': True},
                                            'fields': 'bold'
                                        }
                                    })
                if header_bold_reqs:
                    service.documents().batchUpdate(
                        documentId=doc_id,
                        body={'requests': header_bold_reqs}
                    ).execute()
                    
            except Exception as table_err:
                print(f"[Docs Table] Table insertion failed (non-fatal): {table_err}")
        
        doc_link = f"https://docs.google.com/document/d/{doc_id}/edit"
        if lang == "en":
            return f"✅ Document created successfully: {doc_link}"
        elif lang == "ms":
            return f"✅ Dokumen berjaya dicipta: {doc_link}"
        return f"✅ 文档已创建完成: {doc_link}"
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_docs": ""}})
            if lang == "en":
                return "⚠️ Document creation failed: API permission denied. Sidebar switch has been reset, please re-enable it."
            elif lang == "ms":
                return "⚠️ Penciptaan dokumen gagal: Kebenaran API ditolak. Suis bar sisi telah ditetapkan semula."
            return "⚠️ 创建文档失败：API 权限验证未通过。侧边栏开关已被清理，请重新开启开关授权。"
            
        err_reason = getattr(error, 'reason', str(error))
        if lang == "en":
            return f"⚠️ Document creation failed. Reason: {err_reason}"
        elif lang == "ms":
            return f"⚠️ Penciptaan dokumen gagal. Sebab: {err_reason}"
        return f"⚠️ 创建文档失败，原因：{err_reason}"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Unexpected error: {str(e)}"
        elif lang == "ms":
            return f"⚠️ Ralat tidak dijangka: {str(e)}"
        return f"⚠️ 发生未知错误：{str(e)}"

def _rows_from_sheet_content(content: str) -> list:
    """Convert markdown/CSV/plain text into rows suitable for Sheets values.update."""
    import csv
    from io import StringIO

    text = (content or "").strip()
    if not text:
        return [["Content"], [""]]

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    table_lines = [line for line in lines if line.startswith("|") and line.endswith("|")]
    if table_lines:
        rows = []
        for line in table_lines:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            # Skip markdown separator rows like | --- | :---: |
            if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                continue
            rows.append(cells)
        if rows:
            return rows

    delimiter = "\t" if any("\t" in line for line in lines) else ","
    if any(delimiter in line for line in lines):
        try:
            parsed = list(csv.reader(StringIO("\n".join(lines)), delimiter=delimiter))
            rows = [[cell.strip() for cell in row] for row in parsed if row]
            if rows:
                return rows
        except Exception:
            pass

    return [["Content"], *[[line] for line in lines]]

def _sheet_cell_value(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False)

def _normalize_sheet_rows(rows, content: str = "") -> list:
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except Exception:
            return _rows_from_sheet_content(rows)

    if isinstance(rows, list) and rows:
        if all(isinstance(row, dict) for row in rows):
            headers = []
            for row in rows:
                for key in row.keys():
                    if key not in headers:
                        headers.append(key)
            return [headers] + [[_sheet_cell_value(row.get(header, "")) for header in headers] for row in rows]

        normalized = []
        for row in rows:
            if isinstance(row, list):
                normalized.append([_sheet_cell_value(cell) for cell in row])
            elif isinstance(row, dict):
                normalized.append([json.dumps(row, ensure_ascii=False)])
            else:
                normalized.append([_sheet_cell_value(row)])
        if normalized:
            return normalized

    return _rows_from_sheet_content(content)

_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_DRIVE_METADATA_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"

def _creds_have_scope(creds, scope: str) -> bool:
    scopes = getattr(creds, "scopes", None) or []
    scopes_str = " ".join(scopes) if isinstance(scopes, list) else str(scopes)
    return scope in scopes_str

def _get_sheets_creds(user_id: str):
    creds = get_google_creds_offline(user_id, "sheets")
    if not _creds_have_scope(creds, _SHEETS_SCOPE):
        raise PermissionError("missing_sheets_scope")
    return creds

def _get_sheet_search_creds(user_id: str):
    try:
        creds = get_google_creds_offline(user_id, "sheets")
        if _creds_have_scope(creds, _DRIVE_METADATA_SCOPE) or _creds_have_scope(creds, "https://www.googleapis.com/auth/drive.file"):
            return creds
    except Exception:
        pass
    try:
        creds = get_google_creds_offline(user_id, "drive")
    except Exception as exc:
        raise PermissionError("missing_drive_metadata_scope") from exc
    if not (
        _creds_have_scope(creds, _DRIVE_METADATA_SCOPE)
        or _creds_have_scope(creds, "https://www.googleapis.com/auth/drive.file")
    ):
        raise PermissionError("missing_drive_metadata_scope")
    return creds

def _extract_spreadsheet_id(spreadsheet: str) -> str:
    raw = (spreadsheet or "").strip()
    if not raw:
        return ""
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", raw)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", raw):
        return raw
    return ""

def _drive_query_escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("'", "\\'")

def _quote_sheet_name(sheet_name: str) -> str:
    clean = (sheet_name or "Sheet1").replace("'", "''")
    return f"'{clean}'"

def _build_sheet_range(sheet_name: str = "", range_name: str = "", default_range: str = "A1:Z1000") -> str:
    range_part = (range_name or "").strip()
    sheet_part = (sheet_name or "").strip()
    if "!" in range_part:
        return range_part
    if sheet_part:
        return f"{_quote_sheet_name(sheet_part)}!{range_part or default_range}"
    return range_part or default_range

def _sheet_link(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

def _format_sheet_rows(rows: list, max_rows: int = 40, max_cols: int = 12) -> str:
    if not rows:
        return "(No values found.)"
    clipped = [list(row or [])[:max_cols] for row in rows[:max_rows]]
    width = max((len(row) for row in clipped), default=1)
    normalized = [row + [""] * (width - len(row)) for row in clipped]
    header = [str(cell) if cell != "" else f"Column {idx + 1}" for idx, cell in enumerate(normalized[0])]
    body = normalized[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n... {len(rows) - max_rows} more row(s) not shown.")
    return "\n".join(lines)

def _sheets_permission_message(action: str, lang: str) -> str:
    if lang == "en":
        return f"⚠️ Google Sheets {action} blocked: please enable the Google Sheets switch in Connectors. For searching by file name, re-enable it so Drive metadata permission is included."
    if lang == "ms":
        return f"⚠️ Operasi Google Sheets ({action}) disekat. Sila hidupkan suis Google Sheets dalam Connectors. Untuk carian mengikut nama fail, hidupkan semula supaya kebenaran metadata Drive disertakan."
    return f"⚠️ Google Sheets {action} 被拦截：请在 Connectors 里开启 Google Sheets。若要按文件名搜索，请重新开启一次以包含 Drive metadata 权限。"

def _sheets_not_found_message(target: str, lang: str) -> str:
    if lang == "en":
        return f"⚠️ Could not find a Google Sheet matching: {target or '(empty target)'}"
    if lang == "ms":
        return f"⚠️ Tidak menemui Google Sheet yang sepadan: {target or '(sasaran kosong)'}"
    return f"⚠️ 没有找到匹配的 Google Sheet：{target or '（空目标）'}"

def _search_sheet_files(user_id: str, query: str = "", limit: int = 10) -> list:
    creds = _get_sheet_search_creds(user_id)
    drive = build("drive", "v3", credentials=creds)
    parts = [f"mimeType='{_SHEETS_MIME}'", "trashed=false"]
    if (query or "").strip():
        parts.append(f"name contains '{_drive_query_escape(query.strip())}'")
    result = drive.files().list(
        q=" and ".join(parts),
        pageSize=max(1, min(int(limit or 10), 20)),
        fields="files(id,name,webViewLink,modifiedTime,owners(displayName,emailAddress))",
        orderBy="modifiedTime desc",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute()
    return result.get("files", [])

def _resolve_spreadsheet_id(user_id: str, spreadsheet: str, lang: str = "zh") -> tuple[str, str, str]:
    sid = _extract_spreadsheet_id(spreadsheet)
    if sid:
        return sid, "", _sheet_link(sid)

    matches = _search_sheet_files(user_id, spreadsheet, limit=10)
    if not matches:
        raise FileNotFoundError(spreadsheet or "")

    wanted = (spreadsheet or "").strip().lower()
    exact = [item for item in matches if (item.get("name") or "").strip().lower() == wanted]
    picked = exact[0] if exact else matches[0]
    return picked.get("id", ""), picked.get("name", ""), picked.get("webViewLink", "")

def tool_sheets_create(user_id: str, title: str, rows=None, content: str = "", sheet_name: str = "Sheet1", lang: str = "zh") -> str:
    from googleapiclient.errors import HttpError
    try:
        creds = _get_sheets_creds(user_id)

        service = build('sheets', 'v4', credentials=creds)
        safe_title = title or "AI Generated Spreadsheet"
        safe_sheet_name = re.sub(r"[:\\/?*\[\]]", "_", sheet_name or "Sheet1")[:100] or "Sheet1"
        range_sheet_name = safe_sheet_name.replace("'", "''")
        spreadsheet = service.spreadsheets().create(
            body={
                "properties": {"title": safe_title},
                "sheets": [{"properties": {"title": safe_sheet_name}}],
            },
            fields="spreadsheetId,spreadsheetUrl,sheets.properties.sheetId"
        ).execute()

        spreadsheet_id = spreadsheet.get("spreadsheetId")
        sheet_id = spreadsheet.get("sheets", [{}])[0].get("properties", {}).get("sheetId", 0)
        values = _normalize_sheet_rows(rows, content)
        if values:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{range_sheet_name}'!A1",
                valueInputOption="USER_ENTERED",
                body={"values": values}
            ).execute()

            header_range = {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": max(len(row) for row in values),
            }
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{
                    "repeatCell": {
                        "range": header_range,
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold"
                    }
                }, {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": 0,
                            "endIndex": max(len(row) for row in values),
                        }
                    }
                }]}
            ).execute()

        sheet_link = spreadsheet.get("spreadsheetUrl") or f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
        if lang == "en":
            return f"✅ Spreadsheet created successfully: {sheet_link}"
        elif lang == "ms":
            return f"✅ Hamparan berjaya dicipta: {sheet_link}"
        return f"✅ 表格已创建完成: {sheet_link}"
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_sheets": ""}})
            if lang == "en":
                return "⚠️ Spreadsheet creation failed: API permission denied. Sidebar switch has been reset, please re-enable it."
            elif lang == "ms":
                return "⚠️ Penciptaan hamparan gagal: Kebenaran API ditolak. Suis bar sisi telah ditetapkan semula."
            return "⚠️ 创建表格失败：API 权限验证未通过。侧边栏开关已被清理，请重新开启开关授权。"

        err_reason = getattr(error, 'reason', str(error))
        if lang == "en":
            return f"⚠️ Spreadsheet creation failed. Reason: {err_reason}"
        elif lang == "ms":
            return f"⚠️ Penciptaan hamparan gagal. Sebab: {err_reason}"
        return f"⚠️ 创建表格失败，原因：{err_reason}"
    except Exception as e:
        if isinstance(e, PermissionError):
            return _sheets_permission_message("create", lang)
        if lang == "en":
            return f"⚠️ Unexpected error: {str(e)}"
        elif lang == "ms":
            return f"⚠️ Ralat tidak dijangka: {str(e)}"
        return f"⚠️ 发生未知错误：{str(e)}"

def tool_sheets_search(user_id: str, query: str = "", limit: int = 10, lang: str = "zh") -> str:
    try:
        files = _search_sheet_files(user_id, query, limit)
        if not files:
            return _sheets_not_found_message(query, lang)
        lines = []
        for idx, item in enumerate(files, 1):
            owner = ", ".join(
                (owner.get("displayName") or owner.get("emailAddress") or "")
                for owner in item.get("owners", [])
                if owner
            ).strip(", ")
            line = f"{idx}. {item.get('name', 'Untitled')} - {item.get('webViewLink') or _sheet_link(item.get('id', ''))}"
            if owner:
                line += f" ({owner})"
            lines.append(line)
        if lang == "en":
            return "✅ Matching Google Sheets:\n" + "\n".join(lines)
        if lang == "ms":
            return "✅ Google Sheets yang sepadan:\n" + "\n".join(lines)
        return "✅ 找到这些 Google Sheets：\n" + "\n".join(lines)
    except PermissionError:
        return _sheets_permission_message("search", lang)
    except Exception as e:
        if lang == "en":
            return f"⚠️ Google Sheets search failed: {str(e)}"
        if lang == "ms":
            return f"⚠️ Carian Google Sheets gagal: {str(e)}"
        return f"⚠️ 搜索 Google Sheets 失败：{str(e)}"

def tool_sheets_read(user_id: str, spreadsheet: str, sheet_name: str = "", range_name: str = "", lang: str = "zh") -> str:
    from googleapiclient.errors import HttpError
    try:
        creds = _get_sheets_creds(user_id)
        service = build("sheets", "v4", credentials=creds)
        spreadsheet_id, matched_name, link = _resolve_spreadsheet_id(user_id, spreadsheet, lang)
        meta = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="properties.title,spreadsheetUrl,sheets.properties.title",
        ).execute()
        title = matched_name or meta.get("properties", {}).get("title", "Google Sheet")
        first_sheet = (meta.get("sheets") or [{}])[0].get("properties", {}).get("title", "Sheet1")
        target_range = _build_sheet_range(sheet_name or first_sheet, range_name, "A1:Z1000")
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=target_range,
        ).execute()
        rows = result.get("values", [])
        table = _format_sheet_rows(rows)
        link = meta.get("spreadsheetUrl") or link or _sheet_link(spreadsheet_id)
        if lang == "en":
            return f"✅ Read Google Sheet: {title}\nRange: {target_range}\nLink: {link}\n\n{table}"
        if lang == "ms":
            return f"✅ Google Sheet dibaca: {title}\nJulat: {target_range}\nPautan: {link}\n\n{table}"
        return f"✅ 已读取 Google Sheet：{title}\n范围：{target_range}\n链接：{link}\n\n{table}"
    except PermissionError:
        return _sheets_permission_message("read", lang)
    except FileNotFoundError as e:
        return _sheets_not_found_message(str(e), lang)
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_sheets": ""}})
            return _sheets_permission_message("read", lang)
        err_reason = getattr(error, "reason", str(error))
        if lang == "en":
            return f"⚠️ Google Sheets read failed. Reason: {err_reason}"
        if lang == "ms":
            return f"⚠️ Bacaan Google Sheets gagal. Sebab: {err_reason}"
        return f"⚠️ 读取 Google Sheets 失败，原因：{err_reason}"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Google Sheets read failed: {str(e)}"
        if lang == "ms":
            return f"⚠️ Bacaan Google Sheets gagal: {str(e)}"
        return f"⚠️ 读取 Google Sheets 失败：{str(e)}"

def tool_sheets_append(user_id: str, spreadsheet: str, rows=None, content: str = "", sheet_name: str = "", range_name: str = "", lang: str = "zh") -> str:
    from googleapiclient.errors import HttpError
    try:
        values = _normalize_sheet_rows(rows, content)
        if not values:
            values = [[""]]
        creds = _get_sheets_creds(user_id)
        service = build("sheets", "v4", credentials=creds)
        spreadsheet_id, matched_name, link = _resolve_spreadsheet_id(user_id, spreadsheet, lang)
        if not sheet_name:
            meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title,spreadsheetUrl,properties.title").execute()
            sheet_name = (meta.get("sheets") or [{}])[0].get("properties", {}).get("title", "Sheet1")
            matched_name = matched_name or meta.get("properties", {}).get("title", "")
            link = meta.get("spreadsheetUrl") or link
        target_range = _build_sheet_range(sheet_name, range_name, "A1")
        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=target_range,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
        updated = result.get("updates", {})
        link = link or _sheet_link(spreadsheet_id)
        if lang == "en":
            return f"✅ Appended {updated.get('updatedRows', len(values))} row(s) to Google Sheet {matched_name or spreadsheet}.\nRange: {updated.get('updatedRange', target_range)}\nLink: {link}"
        if lang == "ms":
            return f"✅ {updated.get('updatedRows', len(values))} baris ditambah ke Google Sheet {matched_name or spreadsheet}.\nJulat: {updated.get('updatedRange', target_range)}\nPautan: {link}"
        return f"✅ 已向 Google Sheet「{matched_name or spreadsheet}」追加 {updated.get('updatedRows', len(values))} 行。\n范围：{updated.get('updatedRange', target_range)}\n链接：{link}"
    except PermissionError:
        return _sheets_permission_message("append", lang)
    except FileNotFoundError as e:
        return _sheets_not_found_message(str(e), lang)
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_sheets": ""}})
            return _sheets_permission_message("append", lang)
        err_reason = getattr(error, "reason", str(error))
        if lang == "en":
            return f"⚠️ Google Sheets append failed. Reason: {err_reason}"
        if lang == "ms":
            return f"⚠️ Tambahan Google Sheets gagal. Sebab: {err_reason}"
        return f"⚠️ 追加 Google Sheets 失败，原因：{err_reason}"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Google Sheets append failed: {str(e)}"
        if lang == "ms":
            return f"⚠️ Tambahan Google Sheets gagal: {str(e)}"
        return f"⚠️ 追加 Google Sheets 失败：{str(e)}"

def tool_sheets_update(user_id: str, spreadsheet: str, rows=None, content: str = "", sheet_name: str = "", range_name: str = "A1", lang: str = "zh") -> str:
    from googleapiclient.errors import HttpError
    try:
        values = _normalize_sheet_rows(rows, content)
        if not values:
            values = [[""]]
        creds = _get_sheets_creds(user_id)
        service = build("sheets", "v4", credentials=creds)
        spreadsheet_id, matched_name, link = _resolve_spreadsheet_id(user_id, spreadsheet, lang)
        if not sheet_name:
            meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title,spreadsheetUrl,properties.title").execute()
            sheet_name = (meta.get("sheets") or [{}])[0].get("properties", {}).get("title", "Sheet1")
            matched_name = matched_name or meta.get("properties", {}).get("title", "")
            link = meta.get("spreadsheetUrl") or link
        target_range = _build_sheet_range(sheet_name, range_name or "A1", "A1")
        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=target_range,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
        link = link or _sheet_link(spreadsheet_id)
        if lang == "en":
            return f"✅ Updated Google Sheet {matched_name or spreadsheet}.\nRange: {result.get('updatedRange', target_range)}\nCells: {result.get('updatedCells', 0)}\nLink: {link}"
        if lang == "ms":
            return f"✅ Google Sheet {matched_name or spreadsheet} dikemas kini.\nJulat: {result.get('updatedRange', target_range)}\nSel: {result.get('updatedCells', 0)}\nPautan: {link}"
        return f"✅ 已更新 Google Sheet「{matched_name or spreadsheet}」。\n范围：{result.get('updatedRange', target_range)}\n单元格：{result.get('updatedCells', 0)}\n链接：{link}"
    except PermissionError:
        return _sheets_permission_message("update", lang)
    except FileNotFoundError as e:
        return _sheets_not_found_message(str(e), lang)
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_sheets": ""}})
            return _sheets_permission_message("update", lang)
        err_reason = getattr(error, "reason", str(error))
        if lang == "en":
            return f"⚠️ Google Sheets update failed. Reason: {err_reason}"
        if lang == "ms":
            return f"⚠️ Kemas kini Google Sheets gagal. Sebab: {err_reason}"
        return f"⚠️ 更新 Google Sheets 失败，原因：{err_reason}"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Google Sheets update failed: {str(e)}"
        if lang == "ms":
            return f"⚠️ Kemas kini Google Sheets gagal: {str(e)}"
        return f"⚠️ 更新 Google Sheets 失败：{str(e)}"

def tool_calendar_create(user_id: str, title: str, date_iso: str, lang: str = "zh",
                         description: str = "", duration_minutes: int = 60, location: str = "",
                         user_timezone: str = "") -> str:
    from googleapiclient.errors import HttpError
    try:
        creds = get_google_creds_offline(user_id, "calendar")
        required_scope = 'https://www.googleapis.com/auth/calendar.events'
        if not creds.scopes or required_scope not in creds.scopes:
            if lang == "en":
                return "⚠️ Operation blocked: You are missing Calendar access permissions. 👉 Please enable the switch in the sidebar."
            elif lang == "ms":
                return "⚠️ Operasi disekat: Anda kehilangan kebenaran akses Kalendar. 👉 Sila hidupkan suis di bar sisi."
            return "⚠️ 操作被拦截：缺少 Calendar 访问权限。👉 请在侧边栏中开启开关以授权。"
            
        service = build('calendar', 'v3', credentials=creds)
        
        # Parse start time — strip trailing Z if present
        clean_iso = date_iso.replace('Z', '').strip()
        start_dt = datetime.datetime.fromisoformat(clean_iso)
        end_dt = start_dt + datetime.timedelta(minutes=max(duration_minutes, 15))
        
        # Use user's timezone if provided, else fall back to server config
        from config_loader import cfg
        timezone = user_timezone.strip() if user_timezone and user_timezone.strip() else cfg.timezone
        
        event = {
            'summary': title,
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': timezone,
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': timezone,
            },
        }
        if description:
            event['description'] = description
        if location:
            event['location'] = location
        
        event = service.events().insert(calendarId='primary', body=event).execute()
        
        link = event.get('htmlLink')
        time_str = start_dt.strftime('%Y-%m-%d %H:%M')
        dur_str = f"{duration_minutes} min"
        if lang == "en":
            parts = [f"✅ Calendar event created successfully!",
                     f"📌 **{title}**",
                     f"🕐 {time_str} ({dur_str})"]
            if location: parts.append(f"📍 {location}")
            parts.append(f"🔗 {link}")
            return "\n".join(parts)
        elif lang == "ms":
            parts = [f"✅ Acara Kalendar berjaya dicipta!",
                     f"📌 **{title}**",
                     f"🕐 {time_str} ({dur_str})"]
            if location: parts.append(f"📍 {location}")
            parts.append(f"🔗 {link}")
            return "\n".join(parts)
        parts = [f"✅ 日程已成功创建！",
                 f"📌 **{title}**",
                 f"🕐 {time_str} ({dur_str})"]
        if location: parts.append(f"📍 {location}")
        parts.append(f"🔗 {link}")
        return "\n".join(parts)
        
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_calendar": ""}})
            if lang == "en":
                return "⚠️ Calendar event creation failed: Token expired or unauthorized. Sidebar switch has been reset."
            elif lang == "ms":
                return "⚠️ Penciptaan acara gagal: Token tamat tempoh atau tidak dibenarkan. Suis bar sisi telah ditetapkan semula."
            return "⚠️ 创建日程失败：Token 过期或未授权。侧边栏开关已被重置，请重新开启授权。"
            
        err_reason = getattr(error, 'reason', str(error))
        if lang == "en":
            return f"⚠️ Failed to create calendar event. Reason: {err_reason}"
        elif lang == "ms":
            return f"⚠️ Gagal mencipta acara Kalendar. Sebab: {err_reason}"
        return f"⚠️ 创建日程失败，原因：{err_reason}"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Failed to create calendar event: {str(e)}"
        elif lang == "ms":
            return f"⚠️ Gagal mencipta acara Kalendar: {str(e)}"
        return f"⚠️ 发生未知错误：{str(e)}"

def tool_meet_create(user_id: str, title: str, date_iso: str, lang: str = "zh",
                     description: str = "", duration_minutes: int = 60,
                     participants: list = None, user_timezone: str = "") -> str:
    import random, string
    from googleapiclient.errors import HttpError
    try:
        creds = get_google_creds_offline(user_id, "meet")
        required_scope = 'https://www.googleapis.com/auth/calendar.events'
        if not creds.scopes or required_scope not in creds.scopes:
            if lang == "en":
                return "⚠️ Operation blocked: You are missing Google Meet/Calendar permissions. 👉 Please enable the Meet switch in the sidebar."
            elif lang == "ms":
                return "⚠️ Operasi disekat: Anda kehilangan kebenaran Google Meet/Kalendar. 👉 Sila hidupkan suis Meet di bar sisi."
            return "⚠️ 操作被拦截：缺少 Google Meet/Calendar 访问权限。👉 请在侧边栏中开启 Meet 开关以授权。"

        service = build('calendar', 'v3', credentials=creds)

        clean_iso = date_iso.replace('Z', '').strip()
        start_dt = datetime.datetime.fromisoformat(clean_iso)
        end_dt = start_dt + datetime.timedelta(minutes=max(duration_minutes, 15))

        from config_loader import cfg
        timezone = user_timezone.strip() if user_timezone and user_timezone.strip() else cfg.timezone

        request_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
        event_body = {
            'summary': title,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': timezone},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': timezone},
            'conferenceData': {
                'createRequest': {
                    'requestId': request_id,
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            },
        }
        if description:
            event_body['description'] = description
        if participants:
            event_body['attendees'] = [{'email': p} for p in participants if p]

        event = service.events().insert(
            calendarId='primary',
            body=event_body,
            conferenceDataVersion=1
        ).execute()

        meet_link = event.get('hangoutLink', '')
        cal_link = event.get('htmlLink', '')
        time_str = start_dt.strftime('%Y-%m-%d %H:%M')
        dur_str = f"{duration_minutes} min"

        if lang == "en":
            parts = ["✅ Google Meet created!", f"📌 **{title}**", f"🕐 {time_str} ({dur_str})"]
            if meet_link: parts.append(f"🎥 Meet link: {meet_link}")
            parts.append(f"📅 Calendar: {cal_link}")
            return "\n".join(parts)
        elif lang == "ms":
            parts = ["✅ Google Meet berjaya dicipta!", f"📌 **{title}**", f"🕐 {time_str} ({dur_str})"]
            if meet_link: parts.append(f"🎥 Pautan Meet: {meet_link}")
            parts.append(f"📅 Kalendar: {cal_link}")
            return "\n".join(parts)
        parts = ["✅ Google Meet 已创建！", f"📌 **{title}**", f"🕐 {time_str} ({dur_str})"]
        if meet_link: parts.append(f"🎥 会议链接：{meet_link}")
        parts.append(f"📅 日历链接：{cal_link}")
        return "\n".join(parts)

    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_meet": ""}})
            if lang == "en":
                return "⚠️ Meet creation failed: Token expired or unauthorized. Sidebar switch has been reset."
            elif lang == "ms":
                return "⚠️ Penciptaan Meet gagal: Token tamat tempoh. Suis bar sisi telah ditetapkan semula."
            return "⚠️ 创建 Meet 失败：Token 过期或未授权。侧边栏开关已被重置，请重新开启授权。"
        err_reason = getattr(error, 'reason', str(error))
        if lang == "en":
            return f"⚠️ Failed to create Google Meet. Reason: {err_reason}"
        elif lang == "ms":
            return f"⚠️ Gagal mencipta Google Meet. Sebab: {err_reason}"
        return f"⚠️ 创建 Meet 失败，原因：{err_reason}"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Error: {str(e)}"
        elif lang == "ms":
            return f"⚠️ Ralat: {str(e)}"
        return f"⚠️ 发生错误：{str(e)}"


_SLIDES_SCOPE = "https://www.googleapis.com/auth/presentations"
_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_SLIDES_MIME = "application/vnd.google-apps.presentation"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

_SLIDE_THEME_PRESETS = [
    {
        "name": "midnight",
        "bg": "07111F",
        "card": "0F1F35",
        "accent": "37D6C4",
        "accent2": "7C3AED",
        "title": "FFFFFF",
        "body": "D7E3F4",
        "muted": "8EA2BD",
    },
    {
        "name": "gallery",
        "bg": "F7F5EF",
        "card": "FFFFFF",
        "accent": "E95D35",
        "accent2": "111827",
        "title": "111827",
        "body": "384152",
        "muted": "7B8190",
    },
    {
        "name": "studio",
        "bg": "F6F8FB",
        "card": "FFFFFF",
        "accent": "2563EB",
        "accent2": "06B6D4",
        "title": "101828",
        "body": "344054",
        "muted": "667085",
    },
    {
        "name": "forest",
        "bg": "F3F7F2",
        "card": "FFFFFF",
        "accent": "198754",
        "accent2": "0F3D2E",
        "title": "10251B",
        "body": "31473B",
        "muted": "63756B",
    },
]


def _slides_permission_message(action: str, lang: str = "zh") -> str:
    if lang == "en":
        return f"⚠️ Google Slides {action} blocked: please enable the Google Slides connector again so Slides and Drive file permissions are both granted."
    if lang == "ms":
        return f"⚠️ Operasi Google Slides ({action}) disekat. Sila hidupkan semula penyambung Google Slides supaya kebenaran Slides dan Drive file diberikan."
    return f"⚠️ Google Slides {action} 被拦截：请重新开启 Google Slides 连接器，确保 Slides 和 Drive file 权限都已授权。"


def _get_slides_creds(user_id: str, require_drive: bool = False):
    creds = get_google_creds_offline(user_id, "slides")
    if not _creds_have_scope(creds, _SLIDES_SCOPE):
        raise PermissionError("missing_slides_scope")
    if require_drive and not _creds_have_scope(creds, _DRIVE_FILE_SCOPE):
        raise PermissionError("missing_drive_file_scope")
    return creds


def _safe_slide_title(title: str) -> str:
    clean = re.sub(r"\s+", " ", (title or "").strip())
    return clean[:90] or "AI Generated Presentation"


def _strip_slide_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text or "")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text)
    text = re.sub(r"[*_`>#]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _pick_slide_theme(template_mode: str, title: str) -> dict:
    raw = (template_mode or "").strip().lower()
    if raw in {"dark", "midnight", "premium"}:
        return _SLIDE_THEME_PRESETS[0]
    if raw in {"warm", "gallery", "editorial"}:
        return _SLIDE_THEME_PRESETS[1]
    if raw in {"forest", "green", "nature"}:
        return _SLIDE_THEME_PRESETS[3]
    seed = sum(ord(ch) for ch in (title or raw or "slides"))
    return _SLIDE_THEME_PRESETS[seed % len(_SLIDE_THEME_PRESETS)]


def _normalize_slide_items(slides=None, content: str = "", title: str = "") -> list[dict]:
    normalized = []
    if isinstance(slides, str):
        try:
            slides = json.loads(slides)
        except Exception:
            slides = None

    if isinstance(slides, list):
        for idx, slide in enumerate(slides):
            if isinstance(slide, dict):
                slide_title = _strip_slide_markdown(slide.get("title") or slide.get("heading") or f"Slide {idx + 1}")
                bullets = slide.get("bullets") or slide.get("points") or slide.get("body") or slide.get("content") or []
                if isinstance(bullets, str):
                    bullets = [line for line in re.split(r"\n+|(?<=\.)\s+(?=[A-Z0-9])", bullets) if line.strip()]
                if not isinstance(bullets, list):
                    bullets = [str(bullets)]
            else:
                slide_title = f"Slide {idx + 1}"
                bullets = [str(slide)]
            clean_bullets = [_strip_slide_markdown(str(item))[:190] for item in bullets if _strip_slide_markdown(str(item))]
            normalized.append({"title": slide_title[:80] or f"Slide {idx + 1}", "bullets": clean_bullets[:6]})

    if normalized:
        return normalized[:14]

    sections = []
    current_title = ""
    current_lines = []
    for raw in (content or "").replace("\r\n", "\n").splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^#{2,4}\s+(.+)$", line)
        if heading:
            if current_title or current_lines:
                sections.append((current_title or title, current_lines))
            current_title = _strip_slide_markdown(heading.group(1))
            current_lines = []
            continue
        if line.startswith("# "):
            continue
        current_lines.append(_strip_slide_markdown(line))
    if current_title or current_lines:
        sections.append((current_title or title, current_lines))

    if not sections:
        sentences = [_strip_slide_markdown(item) for item in re.split(r"(?<=[。.!?])\s+", content or "") if _strip_slide_markdown(item)]
        if not sentences:
            sentences = [
                "Overview of the topic",
                "Key points and supporting details",
                "Recommended next steps",
            ]
        sections = [(title or "Overview", sentences)]

    for idx, (section_title, lines) in enumerate(sections[:14]):
        bullets = [line for line in lines if line][:6]
        normalized.append({
            "title": (section_title or f"Slide {idx + 1}")[:80],
            "bullets": bullets or ["Summary"],
        })
    return normalized


def _rgb(hex_color: str) -> dict:
    value = (hex_color or "000000").strip().lstrip("#")
    if len(value) != 6:
        value = "000000"
    return {
        "red": int(value[0:2], 16) / 255,
        "green": int(value[2:4], 16) / 255,
        "blue": int(value[4:6], 16) / 255,
    }


def _slides_size(width: float, height: float) -> dict:
    return {
        "width": {"magnitude": width, "unit": "PT"},
        "height": {"magnitude": height, "unit": "PT"},
    }


def _slides_transform(x: float, y: float) -> dict:
    return {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"}


def _slide_object_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _add_rect_requests(requests: list, page_id: str, x: float, y: float, width: float, height: float, color: str, radius: bool = False) -> str:
    object_id = _slide_object_id("box")
    requests.append({
        "createShape": {
            "objectId": object_id,
            "shapeType": "ROUND_RECTANGLE" if radius else "RECTANGLE",
            "elementProperties": {
                "pageObjectId": page_id,
                "size": _slides_size(width, height),
                "transform": _slides_transform(x, y),
            },
        }
    })
    requests.append({
        "updateShapeProperties": {
            "objectId": object_id,
            "shapeProperties": {
                "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": _rgb(color)}}},
                "outline": {
                    "outlineFill": {"solidFill": {"color": {"rgbColor": _rgb(color)}}},
                    "weight": {"magnitude": 0.1, "unit": "PT"},
                },
            },
            "fields": "shapeBackgroundFill.solidFill.color,outline.outlineFill.solidFill.color,outline.weight",
        }
    })
    return object_id


def _add_text_requests(
    requests: list,
    page_id: str,
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    font_size: float,
    color: str,
    bold: bool = False,
    font_family: str = "Inter",
    alignment: str = "START",
) -> str:
    object_id = _slide_object_id("txt")
    safe_text = (text or "").strip() or " "
    requests.append({
        "createShape": {
            "objectId": object_id,
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": page_id,
                "size": _slides_size(width, height),
                "transform": _slides_transform(x, y),
            },
        }
    })
    requests.append({"insertText": {"objectId": object_id, "insertionIndex": 0, "text": safe_text}})
    requests.append({
        "updateTextStyle": {
            "objectId": object_id,
            "textRange": {"type": "ALL"},
            "style": {
                "fontFamily": font_family,
                "fontSize": {"magnitude": font_size, "unit": "PT"},
                "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(color)}},
                "bold": bool(bold),
            },
            "fields": "fontFamily,fontSize,foregroundColor,bold",
        }
    })
    requests.append({
        "updateParagraphStyle": {
            "objectId": object_id,
            "textRange": {"type": "ALL"},
            "style": {"alignment": alignment},
            "fields": "alignment",
        }
    })
    return object_id


def _build_google_slides_requests(presentation: dict, title: str, slide_items: list[dict], theme: dict) -> list:
    requests = []
    pages = presentation.get("slides") or []
    title_page_id = pages[0].get("objectId") if pages else _slide_object_id("page")
    if not pages:
        requests.append({
            "createSlide": {
                "objectId": title_page_id,
                "insertionIndex": 0,
                "slideLayoutReference": {"predefinedLayout": "BLANK"},
            }
        })

    slide_w, slide_h = 720, 405
    _add_rect_requests(requests, title_page_id, 0, 0, slide_w, slide_h, theme["bg"])
    _add_rect_requests(requests, title_page_id, 44, 52, 88, 7, theme["accent"])
    _add_text_requests(requests, title_page_id, title, 44, 95, 520, 115, 36, theme["title"], bold=True)
    _add_text_requests(requests, title_page_id, "Generated by MSME.AI", 48, 232, 280, 30, 12, theme["muted"], bold=True)
    _add_rect_requests(requests, title_page_id, 565, 38, 88, 88, theme["accent2"], radius=True)
    _add_rect_requests(requests, title_page_id, 612, 96, 66, 66, theme["accent"], radius=True)

    for idx, item in enumerate(slide_items, start=1):
        page_id = _slide_object_id("page")
        requests.append({
            "createSlide": {
                "objectId": page_id,
                "insertionIndex": idx,
                "slideLayoutReference": {"predefinedLayout": "BLANK"},
            }
        })
        _add_rect_requests(requests, page_id, 0, 0, slide_w, slide_h, theme["bg"])
        _add_rect_requests(requests, page_id, 34, 32, 652, 332, theme["card"], radius=True)
        _add_rect_requests(requests, page_id, 34, 32, 8, 332, theme["accent"])
        _add_text_requests(requests, page_id, f"{idx:02d}", 586, 52, 56, 24, 12, theme["muted"], bold=True, alignment="END")
        _add_text_requests(requests, page_id, item.get("title", f"Slide {idx}"), 64, 58, 485, 54, 25, theme["title"], bold=True)
        bullets = [_strip_slide_markdown(line) for line in item.get("bullets", []) if _strip_slide_markdown(line)]
        if not bullets:
            bullets = ["Summary"]
        body_text = "\n".join(f"• {line[:170]}" for line in bullets[:6])
        _add_text_requests(requests, page_id, body_text, 68, 135, 560, 170, 15, theme["body"])
        _add_rect_requests(requests, page_id, 64, 322, 76, 5, theme["accent2"])

    return requests


def tool_slides_create(user_id: str, title: str, content: str = "", slides=None, template_mode: str = "auto", lang: str = "zh") -> str:
    from googleapiclient.errors import HttpError
    try:
        creds = _get_slides_creds(user_id, require_drive=False)
        service = build("slides", "v1", credentials=creds)
        safe_title = _safe_slide_title(title or content)
        slide_items = _normalize_slide_items(slides=slides, content=content, title=safe_title)
        theme = _pick_slide_theme(template_mode, safe_title)
        presentation = service.presentations().create(body={"title": safe_title}).execute()
        presentation_id = presentation.get("presentationId")
        requests = _build_google_slides_requests(presentation, safe_title, slide_items, theme)
        if requests:
            service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={"requests": requests},
            ).execute()
        link = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
        if lang == "en":
            return f"✅ Google Slides presentation created successfully: {link}"
        if lang == "ms":
            return f"✅ Persembahan Google Slides berjaya dicipta: {link}"
        return f"✅ Google Slides 已创建完成：{link}"
    except PermissionError:
        return _slides_permission_message("create", lang)
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_slides": ""}})
            return _slides_permission_message("create", lang)
        err_reason = getattr(error, "reason", str(error))
        if lang == "en":
            return f"⚠️ Google Slides creation failed. Reason: {err_reason}"
        if lang == "ms":
            return f"⚠️ Penciptaan Google Slides gagal. Sebab: {err_reason}"
        return f"⚠️ 创建 Google Slides 失败，原因：{err_reason}"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Unexpected Google Slides error: {str(e)}"
        if lang == "ms":
            return f"⚠️ Ralat Google Slides tidak dijangka: {str(e)}"
        return f"⚠️ Google Slides 发生未知错误：{str(e)}"


def tool_slides_import_generated_file(user_id: str, file_path: str, title: str = "", lang: str = "zh") -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    try:
        if not file_path or not os.path.exists(file_path):
            if lang == "en":
                return "⚠️ No generated PPTX file found to import into Google Slides."
            if lang == "ms":
                return "⚠️ Tiada fail PPTX yang dijana untuk diimport ke Google Slides."
            return "⚠️ 没有找到可导入 Google Slides 的 PPTX 文件。"
        if not file_path.lower().endswith((".pptx", ".ppt")):
            if lang == "en":
                return "⚠️ Google Slides import only supports the latest generated PPTX/PPT file."
            if lang == "ms":
                return "⚠️ Import Google Slides hanya menyokong fail PPTX/PPT terbaru yang dijana."
            return "⚠️ Google Slides 导入目前只支持最近生成的 PPTX/PPT 文件。"

        creds = _get_slides_creds(user_id, require_drive=True)
        drive = build("drive", "v3", credentials=creds)
        source_name = os.path.splitext(os.path.basename(file_path))[0]
        safe_title = _safe_slide_title(title or source_name or "Imported Presentation")
        media = MediaFileUpload(file_path, mimetype=_PPTX_MIME, resumable=True)
        created = drive.files().create(
            body={"name": safe_title, "mimeType": _SLIDES_MIME},
            media_body=media,
            fields="id,name,webViewLink",
        ).execute()
        presentation_id = created.get("id")
        link = created.get("webViewLink") or f"https://docs.google.com/presentation/d/{presentation_id}/edit"
        if lang == "en":
            return f"✅ PPTX imported and converted to Google Slides: {link}"
        if lang == "ms":
            return f"✅ PPTX berjaya diimport dan ditukar kepada Google Slides: {link}"
        return f"✅ PPTX 已导入并转换成 Google Slides：{link}"
    except PermissionError:
        return _slides_permission_message("import", lang)
    except HttpError as error:
        if error.resp.status in [401, 403]:
            users_col.update_one({"_id": user_id}, {"$unset": {"google_creds_slides": ""}})
            return _slides_permission_message("import", lang)
        err_reason = getattr(error, "reason", str(error))
        if lang == "en":
            return f"⚠️ Google Slides import failed. Reason: {err_reason}"
        if lang == "ms":
            return f"⚠️ Import Google Slides gagal. Sebab: {err_reason}"
        return f"⚠️ 导入 Google Slides 失败，原因：{err_reason}"
    except Exception as e:
        if lang == "en":
            return f"⚠️ Unexpected Google Slides import error: {str(e)}"
        if lang == "ms":
            return f"⚠️ Ralat import Google Slides tidak dijangka: {str(e)}"
        return f"⚠️ Google Slides 导入发生未知错误：{str(e)}"


# --- Schema Definitions ---
GOOGLE_WORKSPACE_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "gmail_send",
            "description": "Send an email. Use when user wants to send an email, including sending the latest generated PDF or image/photo as an attachment. IMPORTANT: If you want to write your PREVIOUS long analysis into the email, simply set 'body' to 'USE_PREVIOUS_ANALYSIS'. The backend will automatically inject your analysis text into the email for you. If the user wants you to write a NEW short text, put that new text directly into 'body'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"}
                },
                "required": ["recipient", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_create",
            "description": "Schedule a meeting or calendar event. Convert relative dates (e.g. 'tomorrow', 'next Monday') to absolute ISO format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title/summary"},
                    "date_iso": {"type": "string", "description": "Start time in ISO format WITHOUT timezone suffix, e.g. 2026-04-20T14:00:00"},
                    "duration_minutes": {"type": "integer", "description": "Event length in minutes. Default 60."},
                    "description": {"type": "string", "description": "Event description or agenda notes"},
                    "location": {"type": "string", "description": "Event location (office, meeting room, address, or online link)"}
                },
                "required": ["title", "date_iso"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "meet_create",
            "description": "Create a Google Meet video meeting by creating a Calendar event with a Meet conference link. Use this when the user asks to create, schedule, or set up a Google Meet, video call, video conference, or online meeting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Meeting title/summary"},
                    "date_iso": {"type": "string", "description": "Start time in ISO format WITHOUT timezone suffix, e.g. 2026-04-20T14:00:00"},
                    "duration_minutes": {"type": "integer", "description": "Meeting length in minutes. Default 60."},
                    "description": {"type": "string", "description": "Meeting agenda or notes"},
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional attendee email addresses."
                    }
                },
                "required": ["title", "date_iso"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docs_create",
            "description": "Create a new Google Doc and write text into it. This tool directly connects to the user's Google Docs. IMPORTANT: If you want to write your PREVIOUS long analysis into the document, simply set 'content' to 'USE_PREVIOUS_ANALYSIS'. The backend will automatically inject your analysis text into the document for you. If the user wants you to write a NEW short text, put that new text directly into 'content'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_create",
            "description": "Create a new Google Sheet and write table data into it. Use when the user asks to create, save, export, or write data/table/analysis into Google Sheets. If writing the PREVIOUS analysis or report, set 'content' to 'USE_PREVIOUS_ANALYSIS'. For new structured data, prefer 'rows' as an array of arrays where the first row is headers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "sheet_name": {"type": "string"},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": ["string", "number", "boolean", "null"]}
                        },
                        "description": "2D table values. First row should be headers when possible."
                    },
                    "content": {"type": "string", "description": "Markdown table, CSV, TSV, plain text, or USE_PREVIOUS_ANALYSIS."}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_search",
            "description": "Search existing Google Sheets files by title/name. Use when the user asks to find, list, locate, or search for a spreadsheet before reading or editing it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Spreadsheet title or partial title to search for."},
                    "limit": {"type": "integer", "description": "Maximum number of matching spreadsheets to return. Default 10."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_read",
            "description": "Read values from an existing Google Sheet. Accepts a spreadsheet URL, ID, or title/name. Use when the user asks to inspect, read, check, summarize, or view spreadsheet data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet": {"type": "string", "description": "Spreadsheet URL, ID, exact title, or partial title."},
                    "sheet_name": {"type": "string", "description": "Tab/sheet name. Optional; defaults to the first tab."},
                    "range": {"type": "string", "description": "A1 range such as A1:D20 or Sheet1!A1:D20. Optional; defaults to A1:Z1000."}
                },
                "required": ["spreadsheet"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_append",
            "description": "Append new rows to an existing Google Sheet without overwriting existing data. Use when the user asks to add rows, append data, or insert new records at the bottom.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet": {"type": "string", "description": "Spreadsheet URL, ID, exact title, or partial title."},
                    "sheet_name": {"type": "string", "description": "Tab/sheet name. Optional; defaults to the first tab."},
                    "range": {"type": "string", "description": "A1 anchor/range. Optional; defaults to A1."},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": ["string", "number", "boolean", "null"]}
                        },
                        "description": "Rows to append."
                    },
                    "content": {"type": "string", "description": "Markdown table, CSV, TSV, or plain text to append when rows are not supplied."}
                },
                "required": ["spreadsheet"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_update",
            "description": "Update or replace values in a specific range of an existing Google Sheet. Use when the user asks to modify, change, replace, correct, or write values into specific cells/ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet": {"type": "string", "description": "Spreadsheet URL, ID, exact title, or partial title."},
                    "sheet_name": {"type": "string", "description": "Tab/sheet name. Optional; defaults to the first tab."},
                    "range": {"type": "string", "description": "A1 range to update, such as B2:D4. Defaults to A1."},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": ["string", "number", "boolean", "null"]}
                        },
                        "description": "Replacement values."
                    },
                    "content": {"type": "string", "description": "Markdown table, CSV, TSV, or plain text to write when rows are not supplied."}
                },
                "required": ["spreadsheet"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "slides_create",
            "description": "Create a native Google Slides presentation directly in the user's Google Slides from a prompt or outline. Use when the user asks to create, generate, make, or write a presentation/deck in Google Slides. If the user asks to use any/random/template style, set template_mode to 'auto' and provide a polished slides array.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Presentation title."},
                    "template_mode": {"type": "string", "description": "Optional visual style: auto, studio, midnight, gallery, forest."},
                    "content": {"type": "string", "description": "Markdown outline or USE_PREVIOUS_ANALYSIS when the user wants the previous analysis converted to slides."},
                    "slides": {
                        "type": "array",
                        "description": "Structured slide outline. Prefer 5-10 concise slides for new decks.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "bullets": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                }
                            },
                            "required": ["title", "bullets"]
                        }
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "slides_import_generated_file",
            "description": "Import and convert the latest locally generated PPTX/PPT skill file into a native Google Slides presentation. Use when the user says to save/import/upload/convert the generated PPT, PPTX, presentation, slide deck, or skill-generated slide into Google Slides.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Optional Google Slides file title."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "drive_upload",
            "description": "Upload the latest generated file, PDF, image, photo, PNG, or JPG to Google Drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_name": {"type": "string"}
                },
                "required": []
            }
        }
    }
]
