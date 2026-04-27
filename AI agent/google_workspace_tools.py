import os
import sys
import datetime
import json
import base64
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from pymongo import MongoClient
import jwt

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config_loader import cfg

connectors_router = APIRouter()

# MongoDB setup
mongo_client = MongoClient(cfg.mongo_uri)
db = mongo_client[cfg.mongo_database]
users_col = db["users"]

os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
# Relax scope requirements for granular Google token exchange

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
    'https://www.googleapis.com/auth/calendar.events'
]

def _google_oauth_client_config() -> dict:
    """Return the OAuth web-client config that must match GIS initCodeClient."""
    if GOOGLE_OAUTH_CLIENT_SECRET:
        return {
            "web": {
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": ["postmessage", "http://localhost"],
                "javascript_origins": ["http://localhost", "http://127.0.0.1"],
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

class AuthCodeRequest(BaseModel):
    auth_code: str
    redirect_uri: str = "postmessage" # Usually for frontend initCodeClient we use 'postmessage'
    service_id: str = None # Add context of which service was just authorized

class ConnectorToggleRequest(BaseModel):
    service: str
    enabled: bool

def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

@connectors_router.post("/api/connectors/exchange_code")
async def exchange_auth_code(req: AuthCodeRequest, user_id: str = Depends(get_current_user)):
    """Exchange offline auth code for a refresh token and access token"""
    try:
        flow = Flow.from_client_config(
            _google_oauth_client_config(), scopes=SCOPES, redirect_uri=req.redirect_uri
        )
        flow.fetch_token(code=req.auth_code)
        credentials = flow.credentials
        
        service_id = req.service_id or "default"
        
        creds_data = {
            "google_token": credentials.token,
            "google_refresh_token": credentials.refresh_token,
            "google_token_expiry": credentials.expiry.isoformat() if credentials.expiry else None,
            "google_token_uri": credentials.token_uri,
            "google_client_id": credentials.client_id,
            "google_client_secret": credentials.client_secret,
            "google_scopes": credentials.scopes,
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
        raise HTTPException(status_code=400, detail=str(e))

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
        has_token = bool(creds.get("google_refresh_token") or creds.get("google_token"))
        active = has_token and marker in scopes_str
        status_map[service]["granted"] = active
        status_map[service]["active"] = active

    # Legacy single-token users can still show the matching connector as active.
    if user.get("google_refresh_token") or user.get("google_token"):
        scopes = user.get("google_scopes", [])
        scopes_str = " ".join(scopes) if isinstance(scopes, list) else str(scopes)
        for service, marker in scope_markers.items():
            if marker in scopes_str:
                status_map[service]["granted"] = True
                status_map[service]["active"] = True

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
        # Fallback to the previous temporary workflow token if offline auth isn't setup
        if user and user.get("google_access_token"):
            return Credentials(token=user.get("google_access_token"))
        # Fallback to legacy structure for old users
        if user and user.get("google_refresh_token"):
            creds_dict = user
        else:
            raise ValueError(f"Google Workspace {service_id} not authorized")

    creds = Credentials(
        token=creds_dict.get("google_token"),
        refresh_token=creds_dict.get("google_refresh_token"),
        token_uri=creds_dict.get("google_token_uri"),
        client_id=creds_dict.get("google_client_id"),
        client_secret=creds_dict.get("google_client_secret"),
        scopes=creds_dict.get("google_scopes")
    )
    
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        users_col.update_one(
            {"_id": user_id}, 
            {"$set": {
                f"google_creds_{service_id}.google_token": creds.token,
                "google_token": creds.token # legacy fallback
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
            
            msg = MIMEMultipart()
            msg['To'] = to
            msg['Subject'] = subject if subject else "Pepper Chat Analysis Report"
            msg.attach(MIMEText(body, 'plain'))
            
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
            message = EmailMessage()
            message.set_content(body)
            message['To'] = to
            message['Subject'] = subject if subject else "Pepper Chat Analysis Report"
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


# --- Schema Definitions ---
GOOGLE_WORKSPACE_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "gmail_send",
            "description": "Send an email. Use when user wants to send an email or output to someone. IMPORTANT: If you want to write your PREVIOUS long analysis into the email, simply set 'body' to 'USE_PREVIOUS_ANALYSIS'. The backend will automatically inject your analysis text into the email for you. If the user wants you to write a NEW short text, put that new text directly into 'body'.",
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
            "name": "drive_upload",
            "description": "Upload generated file/PDF to Google Drive.",
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
