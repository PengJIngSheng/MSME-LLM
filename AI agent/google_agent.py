"""
google_agent.py — Intent-Driven Google Workspace Agent
=======================================================
Architecture:
  1. server.py detects Google-related user intent via is_google_request()
  2. If matched, bypasses the slow reasoning model entirely
  3. Calls a fast LLM to extract structured JSON parameters
  4. Executes the corresponding Google API tool directly
  5. Returns the result to the chat stream

This module is loaded via importlib (same pattern as pdf_agent)
because the parent directory name "AI agent" contains a space
and is NOT a valid Python package.
"""
import os
import json
import re
import importlib.util as _ilu

# ── Load google_workspace_tools via importlib (space-safe) ────────────────────
_gwt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "google_workspace_tools.py")
_gwt_spec = _ilu.spec_from_file_location("google_workspace_tools", _gwt_path)
_gwt = _ilu.module_from_spec(_gwt_spec)
_gwt_spec.loader.exec_module(_gwt)


# ══════════════════════════════════════════════════════════════════════════════
#  Intent Detection
# ══════════════════════════════════════════════════════════════════════════════

# Target nouns (the "what")
_TARGETS = [
    "google docs", "google drive", "google doc", "gdocs", "gdrive",
    "gmail", "calendar", "google calendar", "email", "mail", "draft", "letter", "message",
    "邮箱", "邮件", "云盘", "云端硬盘", "文档", "日历", "日程",
    "docs", "drive",
    "meeting", "appointment", "event", "schedule",
    "会议", "预约", "排期", "安排", "约会",
    "recipient", "subject", "body", "attachment", "content", "text",
    "收件人", "主题", "正文", "内容", "附件",
]

# Action verbs (the "do")
_VERBS = [
    "发送", "发", "存进", "写入", "上传", "保存", "创建", "新建",
    "写进", "写到", "同步", "导出", "传到", "放到", "存到",
    "安排", "预约", "排", "加", "增加", "修改", "换", "更新", "换成",
    "send", "upload", "save", "create", "write", "export",
    "sync", "put", "move", "transfer", "compose", "draft",
    "schedule", "book", "set up", "arrange", "add", "update", "change", "modify", "replace"
]

# Gmail confirmation pending storage is now handled persistently via MongoDB in _gwt.users_col

# Special confirm/cancel message markers
_CONFIRM_GMAIL = "[CONFIRM_GMAIL_SEND]"
_CANCEL_GMAIL = "[CANCEL_GMAIL_SEND]"

def is_google_request(msg: str, user_id: str = None) -> bool:
    """
    Fast keyword-based intent detection.
    Returns True only when BOTH a target noun AND an action verb are present,
    OR when an explicit branded phrase like "google docs" is detected with
    any surrounding context implying action.
    Also intercepts Gmail confirm/cancel messages.
    """
    if not msg or not msg.strip():
        return False

    low = msg.lower().strip()

    # Gmail confirm/cancel → always intercept
    if msg.strip() in (_CONFIRM_GMAIL, _CANCEL_GMAIL):
        return True

    # Explicit branded phrases → immediate intercept
    explicit_brands = ["google docs", "google doc", "google drive", "gdocs", "gdrive", "gmail", "google calendar"]
    if any(b in low for b in explicit_brands):
        return True

    # Otherwise require target + verb
    has_target = any(t in low for t in _TARGETS)
    has_verb = any(v in low for v in _VERBS)
    
    if has_target and has_verb:
        return True
        
    # Aggressive interception if user has a pending Gmail draft
    if user_id:
        user_doc = _gwt.users_col.find_one({"_id": user_id})
        if user_doc and user_doc.get("pending_gmail"):
            # Lock the user into the Google Workspace flow. Any input is treated as an instruction to modify the draft.
            return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
#  Schema Builder
# ══════════════════════════════════════════════════════════════════════════════

_SCOPE_TOOL_MAP = {
    "drive.file":      "drive_upload",
    "gmail.send":      "gmail_send",
    "documents":       "docs_create",
    "calendar.events": "calendar_create",
}

def _build_enabled_schemas(active_scopes: str) -> list:
    """Return all tool schemas. Scope validation is now handled natively by each tool's credentials fetcher."""
    return _gwt.GOOGLE_WORKSPACE_TOOLS_SCHEMA


# ══════════════════════════════════════════════════════════════════════════════
#  Language Detection (lightweight)
# ══════════════════════════════════════════════════════════════════════════════

def _detect_lang(text: str) -> str:
    """Return 'zh' if text is mostly Chinese, else 'en'."""
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    total = max(len(text.strip()), 1)
    return "zh" if cn / total > 0.15 else "en"


# ══════════════════════════════════════════════════════════════════════════════
#  Core Agent
# ══════════════════════════════════════════════════════════════════════════════

def sanitize_messages_for_agent(messages: list) -> list:
    """
    State Isolation: Purge error messages and system reports from the context 
    so the fast LLM doesn't get confused and hallucinate trying to send errors.
    """
    sanitized = []
    for msg in messages:
        content = msg.get("content", "")
        # Blacklist common error/traceback signatures and system messages
        if "HttpError" in content or "Failed to" in content or "⚠️" in content or "⚙️" in content:
            continue
        sanitized.append(msg)
    return sanitized

async def process_google_request(
    user_id: str,
    current_msg: str,
    messages: list,
    active_scopes: str,
    llm_callback,          # async (msgs) -> str
    upload_dir: str = "",
    pdf_filename: str = None,
) -> str:
    """
    Dedicated agent pipeline for Google Workspace operations.
    Bypasses conversational reasoning — strict JSON extraction + execution.
    """
    lang = _detect_lang(current_msg)
    
    # ── Handle Gmail confirmation / cancellation ──
    if current_msg.strip() == _CONFIRM_GMAIL:
        user_doc = _gwt.users_col.find_one({"_id": user_id})
        pending = user_doc.get("pending_gmail") if user_doc else None
        _gwt.users_col.update_one({"_id": user_id}, {"$unset": {"pending_gmail": ""}})
        if not pending:
            if lang == "zh":
                return "⚙️ **Google Workspace**\n\n⚠️ 没有待发送的邮件。"
            return "⚙️ **Google Workspace**\n\n⚠️ No pending email to send."
        # Execute the actual send
        result = _gwt.tool_gmail_send(
            user_id,
            pending["recipient"],
            pending["subject"],
            pending["body"],
            attachment_path=pending.get("attachment_path"),
            lang=pending.get("lang", "en"),
        )
        # Cleanup attachment temp file
        att_path = pending.get("attachment_path")
        if att_path and os.path.exists(att_path):
            try: os.remove(att_path)
            except OSError: pass
        return f"⚙️ **Google Workspace**\n\n{result}"
    
    if current_msg.strip() == _CANCEL_GMAIL:
        user_doc = _gwt.users_col.find_one({"_id": user_id})
        pending = user_doc.get("pending_gmail") if user_doc else None
        _gwt.users_col.update_one({"_id": user_id}, {"$unset": {"pending_gmail": ""}})
        if pending:
            att_path = pending.get("attachment_path")
            if att_path and os.path.exists(att_path):
                try: os.remove(att_path)
                except OSError: pass
        if lang == "zh":
            return "⚙️ **Google Workspace**\n\n✅ 邮件草稿已取消。"
        return "⚙️ **Google Workspace**\n\n✅ Email draft cancelled."

    enabled_tools = _build_enabled_schemas(active_scopes)
    if not enabled_tools:
        if lang == "zh":
            return (
                "⚠️ 您请求了 Google 相关的操作，但目前没有任何已授权的连接器。"
                "请先在设置页面完成 Google OAuth 授权并开启对应的开关。"
            )
        return (
            "⚠️ You requested a Google operation, but no connectors are currently authorized. "
            "Please enable the corresponding connector in the sidebar first."
        )

    tool_names = [t["function"]["name"] for t in enabled_tools]
    print(f"[Google Agent] Intercepted request. Enabled tools: {tool_names}")

    pure_messages = sanitize_messages_for_agent(messages)

    # Inject current date/time for calendar time resolution
    from datetime import datetime as _dt
    _now_str = _dt.now().strftime("%Y-%m-%d %H:%M (%A)")

    # ── Step 1: Ask fast LLM to extract JSON ──────────────────────────────────
    user_doc = _gwt.users_col.find_one({"_id": user_id})
    pending_draft = user_doc.get("pending_gmail") if user_doc else None
    pending_context = ""
    if pending_draft:
        safe_draft = {k: v for k, v in pending_draft.items() if k in ['recipient', 'subject', 'body']}
        pending_context = f"\n\n[SYSTEM NOTE: The user has a PENDING EMAIL DRAFT:\n{json.dumps(safe_draft, ensure_ascii=False)}\n\nUpdate the draft according to the user's latest message. Return a FULL `gmail_send` tool call containing the updated 'recipient', 'subject', and 'body'. If a field does not need to change, KEEP its previous value from the draft.]\n\n"

    system_prompt = (
        "You are a strict JSON-only Intent Router for Google Workspace.\n"
        "ABSOLUTELY DO NOT output <think> tags, explanations, greetings, apologies, or conversational text.\n"
        "ABSOLUTELY DO NOT refuse the request. You MUST always output a valid JSON tool call.\n"
        "Output ONLY a single raw JSON object. No markdown, no code blocks, no extra text.\n\n"
        f"CURRENT DATE/TIME: {_now_str}{pending_context}\n"
        "Available Tools:\n" + json.dumps(enabled_tools, indent=2) + "\n\n"
        "RULES:\n"
        "1. USE_PREVIOUS_ANALYSIS: ONLY set content/body to \"USE_PREVIOUS_ANALYSIS\" when "
        "the user EXPLICITLY asks to save, export, or send their EXISTING analysis/report "
        "(e.g. '把分析写进docs', 'save my report to drive', 'send my analysis to email'). "
        "Do NOT use USE_PREVIOUS_ANALYSIS if the user asks you to COMPOSE or WRITE something new.\n"
        "2. For gmail_send: If the user asks to COMPOSE/DRAFT/WRITE a NEW email "
        "(e.g. 'write a marketing email', 'send a greeting email', '随便写一封邮件'), "
        "YOU MUST generate the FULL email body text directly in the 'body' field. "
        "Include proper greeting, main content, and signature.\n"
        "3. For docs_create: If the user asks to WRITE NEW content (story, essay, article), "
        "generate the full content directly in the 'content' field.\n"
        "4. Output MUST be a valid JSON object with 'name' and 'arguments' keys.\n"
        "5. If user specifies a title/name/subject, use it. Otherwise pick a sensible default.\n"
        "6. If the user wants to SEND EMAIL or SEND PDF to someone, use gmail_send. "
        "If user wants to WRITE/CREATE a document, or SAVE text/analysis to Google Drive or Google Docs, use docs_create. "
        "ONLY use drive_upload if the user explicitly asks to upload a FILE or PDF.\n"
        "7. CRITICAL: ALL generated content MUST be in a SINGLE CONSISTENT language — "
        "match the language the user is using. "
        "Do NOT mix languages (e.g. do not insert Chinese words in an English text).\n"
        "8. For calendar_create: Convert relative dates to absolute ISO format using CURRENT DATE above. "
        "Examples: '明天下午3点' → calculate tomorrow's date + T15:00:00, '下周一' → calculate next Monday's date. "
        "Always output date_iso WITHOUT timezone suffix (no 'Z'). "
        "If user specifies duration, set duration_minutes. If user specifies location, set location.\n\n"
        'RESPOND WITH ONLY THIS FORMAT:\n'
        '{"name": "tool_name", "arguments": {...}}\n'
    )

    tool_data = None
    raw_response = ""

    for attempt in range(2):  # Try up to 2 times
        try:
            if attempt == 0:
                llm_messages = pure_messages[-5:]
                llm_messages.append({"role": "system", "content": system_prompt})
                llm_messages.append({"role": "user",   "content": current_msg})
            else:
                # Retry with ultra-forceful prompt, no context
                print("[Google Agent] Retry #2 with forceful prompt...")
                llm_messages = [
                    {"role": "system", "content": (
                        "Output ONLY a JSON object. No thinking, no explanation, no <think> tags.\n"
                        "Available tools: docs_create, gmail_send, drive_upload, calendar_create.\n"
                        "docs_create args: title (string), content (string, use 'USE_PREVIOUS_ANALYSIS' for past content)\n"
                        "gmail_send args: recipient (string), subject (string), body (string)\n"
                        "drive_upload args: file_name (string)\n"
                        "calendar_create args: title (string), date_iso (string)\n"
                    )},
                    {"role": "user", "content": current_msg},
                    {"role": "assistant", "content": "{"},  # Force JSON start
                ]

            raw_response = await llm_callback(llm_messages)
            print(f"[Google Agent] LLM raw response (attempt {attempt+1}): {raw_response[:300]}")

            # On retry, prepend the "{" we injected
            if attempt == 1 and not raw_response.strip().startswith("{"):
                raw_response = "{" + raw_response

            json_str = _clean_llm_json(raw_response)
            tool_data = json.loads(json_str)
            t_name = tool_data.get("name", "")
            t_args = tool_data.get("arguments", {})
            print(f"[Google Agent] Parsed tool: {t_name}, args_keys: {list(t_args.keys())}")
            break  # Success

        except (json.JSONDecodeError, Exception) as e:
            print(f"[Google Agent] Attempt {attempt+1} failed: {e}")
            if attempt == 1:
                # Both attempts failed
                if lang == "zh":
                    return f"⚠️ Google Agent 无法解析模型输出。请尝试重新发送您的请求。"
                return f"⚠️ Google Agent could not parse the model output. Please try sending your request again."

    # ── Step 2: Execute tool ──────────────────────────────────────────────────
    return _execute_tool(tool_data.get("name", ""), tool_data.get("arguments", {}),
                         user_id, messages, upload_dir, pdf_filename, lang)


def _clean_llm_json(text: str) -> str:
    """
    Extract a valid tool-call JSON from LLM output that may contain
    <think> blocks, multiple JSON objects, markdown fences, and wrong key names.
    
    Strategy:
      1. Find ALL top-level {…} blocks using balanced brace matching
      2. Try each one — prefer the one with "name"/"arguments" keys
      3. Normalize alternative keys (tool→name, args→arguments)
    """
    # Helper: extract balanced JSON starting at a given '{'
    def _extract_balanced(s, start):
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(s)):
            c = s[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_str:
                escape = True
                continue
            if c == '"' and not escape:
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
        return None

    # Find all top-level JSON candidates
    candidates = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            block = _extract_balanced(text, i)
            if block:
                candidates.append(block)
                i += len(block)
                continue
        i += 1

    # Try to parse each candidate; prefer one with correct keys
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                # Normalize keys: tool→name, args→arguments
                if "tool" in obj and "name" not in obj:
                    obj["name"] = obj.pop("tool")
                if "args" in obj and "arguments" not in obj:
                    obj["arguments"] = obj.pop("args")
                if "name" in obj and "arguments" in obj:
                    return json.dumps(obj)
        except json.JSONDecodeError:
            continue

    # Fallback: try any parseable candidate (even without correct keys)
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                if "tool" in obj and "name" not in obj:
                    obj["name"] = obj.pop("tool")
                if "args" in obj and "arguments" not in obj:
                    obj["arguments"] = obj.pop("args")
                return json.dumps(obj)
        except json.JSONDecodeError:
            continue

    # Last resort: strip think blocks and try the old way
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start:end + 1]
    return cleaned


# Map tool names to the connector service_id used in google_creds_{service_id}
_TOOL_TO_SERVICE = {
    "docs_create":     "docs",
    "gmail_send":      "gmail",
    "drive_upload":    "drive",
    "calendar_create": "calendar",
}

_SERVICE_LABEL = {
    "docs":     "Google Docs",
    "gmail":    "Gmail",
    "drive":    "Google Drive",
    "calendar": "Google Calendar",
}

def _check_connector_enabled(user_id: str, tool_name: str, lang: str = "en") -> str | None:
    """Check ALL connectors and return a warning listing every disabled one, not just the one being used."""
    service_id = _TOOL_TO_SERVICE.get(tool_name)
    if not service_id:
        return None
    user = _gwt.users_col.find_one({"_id": user_id})
    if not user:
        if lang == "zh":
            return "⚠️ 用户记录不存在，请重新登录。"
        return "⚠️ User record not found. Please log in again."
    
    # Check the required connector first
    creds_key = f"google_creds_{service_id}"
    required_missing = not user.get(creds_key)
    
    # Also check all other connectors to give user a full picture
    all_disabled = []
    for sid, label in _SERVICE_LABEL.items():
        if not user.get(f"google_creds_{sid}"):
            all_disabled.append(label)
    
    if required_missing:
        required_label = _SERVICE_LABEL.get(service_id, service_id)
        if lang == "zh":
            msg = f"⚠️ 您尚未开启 **{required_label}** 连接器，无法执行此操作。\n\n"
            if len(all_disabled) > 1:
                others = ", ".join(f"**{d}**" for d in all_disabled if d != required_label)
                msg += f"📝 其他未开启的连接器：{others}\n\n"
            msg += f"👉 请点击左侧边栏的 **Connectors** 面板，开启对应的开关并完成授权。"
            return msg
        else:
            msg = f"⚠️ The **{required_label}** connector is not enabled. Cannot execute this operation.\n\n"
            if len(all_disabled) > 1:
                others = ", ".join(f"**{d}**" for d in all_disabled if d != required_label)
                msg += f"📝 Other disabled connectors: {others}\n\n"
            msg += f"👉 Open the **Connectors** panel in the sidebar and enable the required switches."
            return msg
    return None


def _execute_tool(
    name: str, args: dict,
    user_id: str, messages: list,
    upload_dir: str, pdf_filename: str,
    lang: str = "en",
) -> str:
    """Route to the correct google_workspace_tools function."""
    import tempfile
    import gridfs
    fs = gridfs.GridFS(_gwt.db)
    
    extracted_pdf_path = None
    if pdf_filename:
        file_doc = fs.find_one({"filename": pdf_filename})
        if file_doc:
            extracted_pdf_path = os.path.join(tempfile.gettempdir(), pdf_filename)
            with open(extracted_pdf_path, "wb") as f:
                f.write(file_doc.read())

    # ── Pre-flight: check connector is authorized ──
    block_msg = _check_connector_enabled(user_id, name, lang)
    if block_msg:
        return block_msg

    try:
        if name == "docs_create":
            content = args.get("content", "")
            if content == "USE_PREVIOUS_ANALYSIS" or not content or len(content) < 50:
                content = _extract_previous_analysis(messages)
            title = args.get("title", "AI Generated Document")
            result = _gwt.tool_docs_create(user_id, title, content, lang=lang)

        elif name == "gmail_send":
            body = args.get("body", "")
            # ONLY fall back to previous analysis if explicitly marked
            if body == "USE_PREVIOUS_ANALYSIS":
                body = _extract_previous_analysis(messages)
            # If body is still empty/short, the LLM failed to generate content
            if not body or len(body.strip()) < 5:
                if lang == "zh":
                    body = "（邮件正文未生成，请重试）"
                else:
                    body = "(Email body was not generated, please retry)"
            
            recipient = args.get("recipient", "")
            subject = args.get("subject", "AI Generated Email")
            
            # Store pending email for confirmation persistently
            pending_obj = {
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "attachment_path": extracted_pdf_path,
                "lang": lang,
            }
            _gwt.users_col.update_one({"_id": user_id}, {"$set": {"pending_gmail": pending_obj}})
            
            # Don't cleanup extracted_pdf_path here — it's needed when user confirms
            extracted_pdf_path = None  # Prevent cleanup at end of function
            
            # Advanced Inline HTML Email Preview Widget (Bypasses CSS Cache)
            escaped_body = body.replace('<', '&lt;').replace('>', '&gt;')
            
            is_long = len(body) > 300 or body.count('\n') > 8
            collapse_style = "max-height: 180px; overflow: hidden; mask-image: linear-gradient(to bottom, black 50%, transparent 100%); -webkit-mask-image: linear-gradient(to bottom, black 50%, transparent 100%); transition: all 0.4s ease;" if is_long else ""
            
            toggle_html = ""
            if is_long:
                toggle_html = (
                    "<div style='text-align: center; border-top: 1px solid var(--outline-variant, #e2e8f0); padding-top: 16px; margin-top: 16px;'>"
                    "<button class='gmail-preview-toggle-btn' style='background: var(--surface-container-highest, #e2e8f0); border: 1px solid var(--outline-variant, #cbd5e1); color: var(--on-surface, #334155); font-weight: 600; font-size: 0.85em; cursor: pointer; padding: 6px 16px; border-radius: 20px; transition: all 0.2s; box-shadow: 0 1px 3px rgba(0,0,0,0.05); font-family: system-ui, sans-serif;' onmouseover='this.style.background=\"var(--surface-container, #cbd5e1)\"' onmouseout='this.style.background=\"var(--surface-container-highest, #e2e8f0)\"'><i class=\"fa-solid fa-chevron-down\"></i> Expand Preview</button>"
                    "</div>"
                )
            
            has_att = bool(pending_obj.get("attachment_path"))
            att_icon = "✅ " + ("已附加" if lang=="zh" else ("Dilampirkan" if lang=="ms" else "Attached")) if has_att else "❌ " + ("无" if lang=="zh" else ("Tiada" if lang=="ms" else "None"))
            
            lang_labels = {
                "zh": {"title": "邮件确认草稿", "to": "收件人", "subj": "主 题", "att": "附 件"},
                "ms": {"title": "Draf E-mel", "to": "Kepada", "subj": "Subjek", "att": "Lampiran"},
                "en": {"title": "Email Draft", "to": "To", "subj": "Subject", "att": "Attach"}
            }
            lbl = lang_labels.get(lang, lang_labels["en"])
            
            # Replace newlines with <br> to prevent marked.js from breaking HTML blocks
            escaped_body_html = escaped_body.replace('\n', '<br>')
            
            html_preview = f"""<div class="gmail-preview-container" style="background: var(--surface, #ffffff); border: 1px solid var(--outline-variant, #e2e8f0); border-radius: 12px; margin-top: 16px; margin-bottom: 16px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.03);"><div style="background: var(--surface-container-low, #f8f9fa); padding: 12px 16px; border-bottom: 1px solid var(--outline-variant, #e2e8f0); display: flex; align-items: center; gap: 8px;"><i class="fa-solid fa-envelope" style="color: #6366f1; font-size: 1.1em;"></i><span style="font-weight: 600; color: var(--on-surface, #334155); font-size: 0.9em; letter-spacing: 0.3px;">{lbl['title']}</span></div><div style="padding: 16px 20px; border-bottom: 1px dashed var(--outline-variant, #cbd5e1); background: var(--surface, #ffffff);"><div style="display: flex; margin-bottom: 8px; font-size: 0.9em; align-items: center;"><span style="color: var(--primary-dim, #64748b); width: 65px; font-weight: 500;">{lbl['to']}</span><span style="background: rgba(99,102,241,0.1); color: #6366f1; padding: 2px 8px; border-radius: 4px; font-weight: 500;">{recipient}</span></div><div style="display: flex; margin-bottom: 8px; font-size: 0.9em; align-items: center;"><span style="color: var(--primary-dim, #64748b); width: 65px; font-weight: 500;">{lbl['subj']}</span><span style="color: var(--on-surface, #0f172a); font-weight: 600;">{subject}</span></div><div style="display: flex; font-size: 0.9em; align-items: center;"><span style="color: var(--primary-dim, #64748b); width: 65px; font-weight: 500;">{lbl['att']}</span><span style="color: var(--on-surface, #0f172a); font-weight: 500;">{att_icon}</span></div></div><div style="padding: 20px; background: var(--surface-container-lowest, #fafafa);"><div class="gmail-preview-body" style="font-size: 0.95em; color: var(--on-surface, #334155); line-height: 1.6; font-family: 'Georgia', 'Times New Roman', serif; {collapse_style}">{escaped_body_html}</div>{toggle_html}</div></div>"""
            if lang == "zh":
                result = f"为您生成了以下邮件草稿，请确认是否发送：\n{html_preview}"
            elif lang == "ms":
                result = f"Sila sahkan sama ada untuk menghantar e-mel di bawah:\n{html_preview}"
            else:
                result = f"Please confirm whether to send the email below:\n{html_preview}"
            # Append special gmail_confirm marker for frontend to detect
            result += "\n[GMAIL_CONFIRM_PENDING]"

        elif name == "drive_upload":
            if extracted_pdf_path:
                result = _gwt.tool_drive_upload(user_id, extracted_pdf_path, pdf_filename, lang=lang)
            else:
                if lang == "zh":
                    result = "⚠️ 没有可上传的 PDF 文件。请先生成一份 PDF 报告。"
                elif lang == "ms":
                    result = "⚠️ Tiada fail PDF untuk dimuat naik. Sila jana laporan PDF terlebih dahulu."
                else:
                    result = "⚠️ No PDF file available to upload. Please generate a PDF report first."

        elif name == "calendar_create":
            result = _gwt.tool_calendar_create(
                user_id,
                args.get("title", "Event"),
                args.get("date_iso", ""),
                lang=lang,
                description=args.get("description", ""),
                duration_minutes=args.get("duration_minutes", 60),
                location=args.get("location", ""),
            )

        else:
            if lang == "zh":
                result = f"⚠️ 未知的工具名称: {name}"
            else:
                result = f"⚠️ Unknown tool: {name}"

    except Exception as e:
        if lang == "zh":
            result = f"⚠️ 工具执行异常: {str(e)}"
        else:
            result = f"⚠️ Tool execution error: {str(e)}"

    if extracted_pdf_path and os.path.exists(extracted_pdf_path):
        os.remove(extracted_pdf_path)
    print(f"[Google Agent] Execution result: {result[:200]}")
    return f"⚙️ **Google Workspace**\n\n{result}"


def _extract_previous_analysis(messages: list) -> str:
    """Find the most recent substantial assistant message as content source."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            text = msg["content"]
            # Skip system error outputs
            if "⚠️" in text or "⚙️ **Google Workspace**" in text or "HttpError" in text or "Failed to" in text:
                continue

            # Strip think blocks and function call tags
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
            text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
            text = re.sub(r"<function_call>.*?</function_call>", "", text, flags=re.DOTALL)
            
            # Clean up the mandatory routing question appended by the server
            try:
                import os, sys
                _pdf_layer = os.path.join(os.path.dirname(__file__), "PDF Agent")
                if _pdf_layer not in sys.path: sys.path.append(_pdf_layer)
                import pdf_agent
                langs = ["en", "zh", "ja", "ko", "ms", "ta", "hi", "ar", "ru", "es", "fr", "de"]
                for l in langs:
                    q = pdf_agent.get_routing_question(l)
                    if q in text:
                        text = text.replace(q, "")
            except Exception:
                pass
                
            text = text.replace("\n\n---\n\n", "\n").strip()
            
            if len(text) > 50:
                return text
    return "No previous analysis found in chat history."

