import base64
import html
import io
import json
import os
import re
import uuid
import xml.etree.ElementTree as ET


MODEL_IMAGE_MAX_SIDE = 1536
MODEL_IMAGE_JPEG_QUALITY = 92

IMAGE_GENERATION_PATTERNS = [
    r"\b(generate|create|make|design|draw|illustrate)\b.{0,60}\b(image|picture|photo|illustration|poster|logo|icon|banner|graphic|svg|png|jpe?g|wallpaper)\b",
    r"\b(image|picture|photo|illustration|poster|logo|icon|banner|graphic|svg|png|jpe?g|wallpaper)\b.{0,40}\b(generate|create|make|design|draw)\b",
    r"\bdraw\s+(me\s+)?(a|an|one)?\s+(?!conclusion\b)",
    r"(生成|制作|创建|设计|画|绘制).{0,30}(图片|图像|照片|插画|海报|logo|标志|图标|横幅|壁纸|svg|png|jpg|jpeg)",
    r"(图片|图像|照片|插画|海报|logo|标志|图标|横幅|壁纸|svg|png|jpg|jpeg).{0,30}(生成|制作|创建|设计|画|绘制)",
    r"\b(jana|hasilkan|buat|reka|lukis)\b.{0,50}\b(gambar|imej|foto|poster|logo|ikon|banner|ilustrasi|svg|png|jpe?g)\b",
]

IMAGE_EDIT_PATTERNS = [
    r"\b(change|modify|edit|update|adjust|replace|recolor|rename|make|turn|set)\b.{0,100}\b(background|color|colour|logo|image|picture|photo|style|text|font|shape|object|company\s+name|brand\s+name|name|wording|words?|title|label|typography)\b",
    r"\b(background|color|colour|logo|image|picture|photo|style|text|font|shape|object|company\s+name|brand\s+name|name|wording|words?|title|label|typography)\b.{0,100}\b(change|modify|edit|update|adjust|replace|recolor|rename|make|turn|set)\b",
    r"\b(use|keep|preserve)\b.{0,80}\b(previous|same|this|that|generated)\b.{0,80}\b(image|picture|photo|logo)\b",
    r"(修改|更改|改|调整|替换|换成|改成|变成|设为|保持|保留).{0,50}(背景|颜色|图片|图像|照片|logo|标志|字体|文字|名称|名字|公司名|公司名称|品牌名|品牌名称|风格|形状)",
    r"(背景|颜色|图片|图像|照片|logo|标志|字体|文字|名称|名字|公司名|公司名称|品牌名|品牌名称|风格|形状).{0,50}(修改|更改|改|调整|替换|换成|改成|变成|设为|保持|保留)",
]

VISUAL_ASSET_TERMS = (
    "logo", "poster", "icon", "banner", "wallpaper", "illustration",
    "graphic", "app icon", "cover image", "png", "jpg", "jpeg", "svg",
    "图片", "图像", "照片", "插画", "海报", "标志", "图标",
    "横幅", "壁纸", "封面", "头像", "宣传图", "矢量图",
)

VISUAL_BRIEF_HINTS = (
    "modern", "minimal", "minimalist", "clean", "professional", "tech",
    "futuristic", "white background", "black", "blue", "style", "vector",
    "website", "navbar", "dashboard", "科技", "现代", "简洁", "专业",
    "白色背景", "黑色", "蓝色", "风格", "适合", "导航栏", "网站",
    "高清", "圆角", "扁平", "商业", "未来感", "科技感",
)

NON_GENERATION_QUESTION_HINTS = (
    "what is", "how to", "explain", "analyze", "analyse", "describe",
    "compare", "recommend", "suggest", "idea", "ideas", "prompt",
    "什么是", "怎么", "如何", "解释", "分析", "描述", "识别",
    "建议", "方案", "思路", "提示词",
)

SAFE_SVG_TAGS = {
    "svg", "g", "defs", "path", "rect", "circle", "ellipse", "line",
    "polyline", "polygon", "text", "tspan", "linearGradient",
    "radialGradient", "stop", "title", "desc",
}

SAFE_SVG_ATTRS = {
    "xmlns", "viewBox", "width", "height", "x", "y", "x1", "y1",
    "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "fill",
    "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
    "stroke-dasharray", "opacity", "fill-opacity", "stroke-opacity",
    "transform", "font-size", "font-family", "font-weight", "text-anchor",
    "dominant-baseline", "offset", "stop-color", "stop-opacity", "id",
    "role", "aria-label",
}


def model_supports_vision(model_name: str) -> bool:
    name = (model_name or "").lower()
    return any(marker in name for marker in (
        "gemma4", "gemma-4", "gemma3", "gemma-3",
        "llava", "bakllava", "moondream", "minicpm-v",
        "qwen2-vl", "qwen2.5-vl", "qwen-vl",
        "llama3.2-vision", "llama-3.2-vision", "vision",
    ))


def is_image_generation_request(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    if any(term in low for term in ("analyze image", "analyse image", "describe image", "ocr", "what is in this image")):
        return False
    if any(term in t for term in ("分析图片", "分析这张图", "描述图片", "看图", "识别图片")):
        return False
    if any(re.search(pattern, low if pattern.startswith(r"\b") else t, re.IGNORECASE) for pattern in IMAGE_GENERATION_PATTERNS):
        return True

    has_asset = any(term in low for term in VISUAL_ASSET_TERMS if re.match(r"^[a-z0-9 .+-]+$", term))
    has_asset = has_asset or any(term in t for term in VISUAL_ASSET_TERMS if not re.match(r"^[a-z0-9 .+-]+$", term))
    if not has_asset:
        return False

    looks_like_question = any(term in low for term in NON_GENERATION_QUESTION_HINTS if re.match(r"^[a-z0-9 .+-]+$", term))
    looks_like_question = looks_like_question or any(term in t for term in NON_GENERATION_QUESTION_HINTS if not re.match(r"^[a-z0-9 .+-]+$", term))
    has_brief_hint = any(term in low for term in VISUAL_BRIEF_HINTS if re.match(r"^[a-z0-9 .+-]+$", term))
    has_brief_hint = has_brief_hint or any(term in t for term in VISUAL_BRIEF_HINTS if not re.match(r"^[a-z0-9 .+-]+$", term))
    starts_like_brief = bool(re.match(r"^(a|an|one)\s+", low)) or bool(re.match(r"^(一个|一张|一幅|一款|一套)", t))
    format_requested = bool(re.search(r"\b(svg|png|jpe?g)\b", low))
    comma_brief = len(t) <= 260 and any(mark in t for mark in (",", "，", "、")) and not any(mark in t for mark in ("?", "？"))

    return (has_brief_hint or starts_like_brief or format_requested or comma_brief) and not looks_like_question


def is_image_edit_request(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    if any(term in low for term in ("how to", "explain", "what is", "why does")):
        return False
    if any(term in t for term in ("怎么", "如何", "为什么", "解释")):
        return False
    return any(re.search(pattern, low if pattern.startswith(r"\b") else t, re.IGNORECASE) for pattern in IMAGE_EDIT_PATTERNS)


def latest_generated_image_reference(messages: list, before_index: int | None = None) -> dict:
    end = len(messages or []) if before_index is None else max(0, min(before_index, len(messages or [])))
    scoped = list(messages or [])[:end]
    for idx in range(len(scoped) - 1, -1, -1):
        msg = scoped[idx] or {}
        if msg.get("role") != "assistant":
            continue
        image_name = msg.get("generated_image_name") or ""
        image_url = msg.get("generated_image_url") or ""
        if not image_name and image_url:
            image_name = os.path.basename(image_url.split("?", 1)[0])
        if not image_name:
            continue

        source_prompt = ""
        for prev in range(idx - 1, -1, -1):
            prev_msg = scoped[prev] or {}
            if prev_msg.get("role") == "user":
                source_prompt = prev_msg.get("content", "")
                break

        return {
            "generated_image_name": image_name,
            "generated_image_url": image_url or f"/uploads/{image_name}",
            "source_prompt": source_prompt,
        }
    return {}


def generated_image_reference_attachment(ref: dict) -> dict:
    image_name = (ref or {}).get("generated_image_name") or ""
    if not image_name:
        return {}
    ext = os.path.splitext(image_name)[1].lower()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(ext, "image/png")
    return {
        "file_id": os.path.splitext(image_name)[0],
        "original_name": image_name,
        "saved_path": image_name,
        "url": (ref or {}).get("generated_image_url") or f"/uploads/{image_name}",
        "content_type": content_type,
        "generated_reference": True,
        "source_prompt": (ref or {}).get("source_prompt") or "",
    }


def image_attachments(attachments: list) -> list:
    def _is_image(att: dict) -> bool:
        content_type = str(att.get("content_type", "")).lower()
        name = str(att.get("original_name") or att.get("saved_path") or "").lower()
        return (
            content_type.startswith("image/")
            or re.search(r"\.(png|jpe?g|webp|gif|bmp|svg)$", name) is not None
            or os.path.basename(name).startswith("pasted-image-")
        )

    return [att for att in (attachments or []) if _is_image(att)]


def default_image_analysis_prompt(lang_hint: str = "") -> str:
    if str(lang_hint or "").lower().startswith("zh"):
        return "请分析我上传的图片，描述可见内容，提取图片里的文字，并指出重要细节。"
    if str(lang_hint or "").lower().startswith("ms"):
        return "Sila analisis imej yang saya muat naik, terangkan kandungan yang kelihatan, ekstrak teks, dan nyatakan butiran penting."
    return "Please analyze the uploaded image(s), describe the visible content, extract any text, and point out important details."


def _looks_like_text_heavy_image(name: str, content_type: str) -> bool:
    low = f"{name or ''} {content_type or ''}".lower()
    return any(marker in low for marker in (
        "screenshot", "screen shot", "pasted-image", "receipt", "invoice",
        "bill", "statement", "form", "document", "doc", "table", "chart",
        "graph", "menu", "slide", "poster", "pdf", "截图", "收据", "发票",
        "表格", "图表", "文件",
    )) or "png" in low


def _attachment_filename(att: dict) -> str:
    return att.get("saved_path") or (att.get("file_id", "") + os.path.splitext(att.get("original_name", ""))[1])


def prepare_image_for_model(att: dict, index: int, fs) -> dict:
    filename = _attachment_filename(att)
    file_doc = fs.find_one({"filename": filename})
    if not file_doc:
        raise FileNotFoundError(filename or f"image_{index}")

    raw_bytes = file_doc.read()
    original_name = att.get("original_name") or filename or f"image_{index}"
    content_type = att.get("content_type") or getattr(file_doc, "content_type", "") or "image/*"
    meta = {
        "index": index,
        "name": original_name,
        "content_type": content_type,
        "original_size": len(raw_bytes),
        "model_size": len(raw_bytes),
        "format": content_type.split("/")[-1].upper() if "/" in content_type else "IMAGE",
        "compressed": False,
    }

    try:
        from PIL import Image, ImageOps
    except Exception:
        meta["note"] = "Pillow unavailable; sent original image bytes."
        return {"base64": base64.b64encode(raw_bytes).decode("utf-8"), "meta": meta}

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            original_w, original_h = img.size
            meta["original_width"] = original_w
            meta["original_height"] = original_h

            max_side = max(original_w, original_h)
            if max_side > MODEL_IMAGE_MAX_SIDE:
                scale = MODEL_IMAGE_MAX_SIDE / max_side
                new_size = (max(1, round(original_w * scale)), max(1, round(original_h * scale)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                meta["compressed"] = True

            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                background = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.convert("RGBA").getchannel("A")
                background.paste(img.convert("RGBA"), mask=alpha)
                img = background
            elif img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            model_w, model_h = img.size
            meta["model_width"] = model_w
            meta["model_height"] = model_h

            text_heavy = _looks_like_text_heavy_image(original_name, content_type)
            encoded = io.BytesIO()
            if text_heavy:
                png_img = img.convert("RGB") if img.mode != "RGB" else img
                png_img.save(encoded, format="PNG", optimize=True)
                if encoded.tell() <= 4 * 1024 * 1024:
                    meta["format"] = "PNG"
                    meta["model_size"] = encoded.tell()
                    return {"base64": base64.b64encode(encoded.getvalue()).decode("utf-8"), "meta": meta}
                encoded = io.BytesIO()

            jpg_img = img.convert("RGB") if img.mode != "RGB" else img
            jpg_img.save(encoded, format="JPEG", quality=MODEL_IMAGE_JPEG_QUALITY, optimize=True, progressive=True)
            meta["format"] = "JPEG"
            meta["model_size"] = encoded.tell()
            meta["compressed"] = meta["compressed"] or encoded.tell() < len(raw_bytes)
            return {"base64": base64.b64encode(encoded.getvalue()).decode("utf-8"), "meta": meta}
    except Exception as exc:
        print(f"[IMAGE] Preprocess fallback for {original_name}: {exc}")
        meta["note"] = "Image preprocessing failed; sent original image bytes."
        return {"base64": base64.b64encode(raw_bytes).decode("utf-8"), "meta": meta}


def prepared_images_from_attachments(attachments: list, fs, max_images: int = 6) -> list:
    prepared = []
    for idx, att in enumerate(image_attachments(attachments)[:max_images], 1):
        try:
            prepared.append(prepare_image_for_model(att, idx, fs))
        except Exception as e:
            print(f"[IMAGE] Failed to load image attachment: {e}")
    return prepared


def encoded_images_from_attachments(attachments: list, fs) -> list:
    return [item["base64"] for item in prepared_images_from_attachments(attachments, fs)]


def image_reference_lines(prepared_images: list) -> str:
    lines = []
    for item in prepared_images:
        meta = item.get("meta", {})
        original_dims = ""
        if meta.get("original_width") and meta.get("original_height"):
            original_dims = f", original {meta['original_width']}x{meta['original_height']}"
        model_dims = ""
        if meta.get("model_width") and meta.get("model_height"):
            model_dims = f", model input {meta['model_width']}x{meta['model_height']}"
        size_note = f", {meta.get('model_size', 0) // 1024}KB sent"
        lines.append(
            f"Image {meta.get('index')}: {meta.get('name', 'image')}"
            f" ({meta.get('format', 'IMAGE')}{original_dims}{model_dims}{size_note})"
        )
    return "\n".join(lines)


def image_analysis_protocol(user_question: str, prepared_images: list) -> str:
    q = (user_question or "").lower()
    wants_ocr = any(term in q for term in (
        "ocr", "text", "extract", "read", "transcribe", "文字", "提取", "识别",
        "读", "baca", "teks", "ekstrak",
    ))
    wants_table = any(term in q for term in (
        "table", "chart", "graph", "receipt", "invoice", "bill", "statement",
        "form", "menu", "表格", "图表", "收据", "发票", "账单", "borang",
        "resit", "invois", "jadual", "carta",
    ))
    task_hint = []
    if wants_ocr:
        task_hint.append("The user likely wants OCR/text extraction; preserve line breaks and mark unreadable text as [unclear].")
    if wants_table:
        task_hint.append("The user likely wants structured extraction; reconstruct tables, totals, dates, labels, and units carefully.")
    if len(prepared_images) > 1:
        task_hint.append("For multiple images, compare them and cite details as Image 1, Image 2, etc.")
    if not task_hint:
        task_hint.append("Answer the user's specific visual question first, then add concise supporting details.")

    refs = image_reference_lines(prepared_images)
    return (
        "IMAGE INPUT PROTOCOL:\n"
        f"The latest user message includes {len(prepared_images)} uploaded image(s). They are attached in this exact order:\n"
        f"{refs}\n\n"
        "Analysis rules:\n"
        "- Inspect the image content directly; do not rely on filenames alone.\n"
        "- Start with the direct answer to the user's question.\n"
        "- For visible text/OCR, transcribe exactly, preserve important line breaks, and use [unclear] for doubtful characters.\n"
        "- For tables, receipts, invoices, forms, charts, and screenshots, extract structured fields, numbers, dates, totals, labels, and units.\n"
        "- For charts/graphs, describe axes, legend, trends, outliers, and any readable values.\n"
        "- Mention uncertainty when the image is low-resolution, cropped, blurry, or text is partially hidden.\n"
        "- Do not invent hidden details, identities, exact locations, serial numbers, or prices that are not visible.\n"
        "- Use clean markdown. Use compact tables when they improve readability.\n"
        f"Task hint: {' '.join(task_hint)}"
    )


def _extract_svg_candidate(text: str) -> str:
    cleaned = re.sub(r"```(?:svg|xml)?", "", text or "", flags=re.IGNORECASE).replace("```", "")
    match = re.search(r"<svg\b[\s\S]*?</svg>", cleaned, flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _extract_prompt_from_tool_call(text: str) -> str:
    cleaned = re.sub(r"```(?:json)?", "", text or "", flags=re.IGNORECASE).replace("```", "").strip()
    if not cleaned:
        return ""

    try:
        obj = json.loads(cleaned)
    except Exception:
        obj = None

    if isinstance(obj, dict):
        action_input = obj.get("action_input") or obj.get("input") or obj.get("arguments")
        if isinstance(action_input, str):
            try:
                inner = json.loads(action_input)
                if isinstance(inner, dict) and inner.get("prompt"):
                    return str(inner["prompt"]).strip()
            except Exception:
                match = re.search(r'"prompt"\s*:\s*"((?:\\.|[^"\\])*)"', action_input)
                if match:
                    try:
                        return json.loads(f'"{match.group(1)}"').strip()
                    except Exception:
                        return match.group(1).strip()
        if isinstance(action_input, dict) and action_input.get("prompt"):
            return str(action_input["prompt"]).strip()
        if obj.get("prompt"):
            return str(obj["prompt"]).strip()

    match = re.search(r'"prompt"\s*:\s*"((?:\\.|[^"\\])*)"', cleaned)
    if match:
        try:
            return json.loads(f'"{match.group(1)}"').strip()
        except Exception:
            return match.group(1).strip()
    return ""


def _fallback_svg_from_prompt(prompt: str) -> str:
    clean = re.sub(r"\s+", " ", prompt or "").strip()
    clean = re.sub(r"^(生成|制作|创建|设计|画|绘制)\s*(一张|一个|一幅|一款|一套)?\s*(png|jpg|jpeg|svg)?\s*(图片|图像|照片)?[:：]?\s*", "", clean, flags=re.IGNORECASE)
    title = "MSME.AI" if "msme" in clean.lower() else "Generated Image"
    subtitle = clean[:86] if clean else "AI generated visual"
    if len(clean) > 86:
        subtitle = subtitle.rstrip(" ,，、") + "..."

    title = html.escape(title)
    subtitle = html.escape(subtitle)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#ffffff"/>
<stop offset="1" stop-color="#eef6ff"/>
</linearGradient>
<linearGradient id="accent" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#0b1220"/>
<stop offset="1" stop-color="#0ea5e9"/>
</linearGradient>
</defs>
<rect x="0" y="0" width="1024" height="1024" fill="url(#bg)"/>
<circle cx="770" cy="238" r="124" fill="#dbeafe" opacity="0.82"/>
<circle cx="280" cy="778" r="164" fill="#cffafe" opacity="0.58"/>
<rect x="158" y="230" width="708" height="564" rx="72" fill="#ffffff" stroke="#d7dee8" stroke-width="4"/>
<path d="M276 580 C338 392 466 340 566 430 C640 496 692 448 748 344" fill="none" stroke="url(#accent)" stroke-width="28" stroke-linecap="round"/>
<circle cx="278" cy="580" r="31" fill="#0b1220"/>
<circle cx="566" cy="430" r="31" fill="#0284c7"/>
<circle cx="748" cy="344" r="31" fill="#38bdf8"/>
<line x1="276" y1="676" x2="748" y2="676" stroke="#d7dee8" stroke-width="6" stroke-linecap="round"/>
<text x="512" y="720" font-family="Inter, Arial, sans-serif" font-size="76" font-weight="700" text-anchor="middle" fill="#0b1220">{title}</text>
<text x="512" y="776" font-family="Inter, Arial, sans-serif" font-size="28" font-weight="400" text-anchor="middle" fill="#475569">{subtitle}</text>
</svg>"""


def _sanitize_svg(svg_text: str) -> str:
    svg_text = _extract_svg_candidate(svg_text)
    if not svg_text:
        return ""
    svg_text = re.sub(r"<!DOCTYPE[\s\S]*?>", "", svg_text, flags=re.IGNORECASE)
    svg_text = re.sub(r"<\?xml[\s\S]*?\?>", "", svg_text, flags=re.IGNORECASE)
    svg_text = re.sub(r"<!--[\s\S]*?-->", "", svg_text)
    try:
        root = ET.fromstring(svg_text)
    except Exception as exc:
        print(f"[IMAGE GEN] Invalid SVG from model: {exc}")
        return ""

    def _local(tag: str) -> str:
        return tag.split("}", 1)[-1] if "}" in tag else tag

    def _safe_value(value: str) -> str:
        value = str(value or "").strip()
        if len(value) > 500:
            value = value[:500]
        lower = value.lower()
        if "javascript:" in lower or "data:" in lower or "<" in value or ">" in value:
            return ""
        return value

    def _clean_node(node):
        tag = _local(node.tag)
        if tag not in SAFE_SVG_TAGS:
            return None
        clean = ET.Element(tag)
        for key, value in (node.attrib or {}).items():
            attr = _local(key)
            if attr.startswith("on") or attr not in SAFE_SVG_ATTRS:
                continue
            safe = _safe_value(value)
            if safe:
                clean.set(attr, safe)
        if tag in {"text", "tspan", "title", "desc"} and node.text:
            clean.text = node.text[:240]
        for child in list(node):
            clean_child = _clean_node(child)
            if clean_child is not None:
                clean.append(clean_child)
        return clean

    clean_root = _clean_node(root)
    if clean_root is None or _local(clean_root.tag) != "svg":
        return ""
    clean_root.set("xmlns", "http://www.w3.org/2000/svg")
    clean_root.set("width", "1024")
    clean_root.set("height", "1024")
    if not clean_root.get("viewBox"):
        clean_root.set("viewBox", "0 0 1024 1024")
    return ET.tostring(clean_root, encoding="unicode", method="xml")


def requested_image_format(text: str) -> str:
    raw = text or ""
    low = raw.lower()
    if re.search(r"\bsvg\b", low) or any(term in raw for term in ("矢量图", "矢量", "向量图")):
        return "svg"
    if re.search(r"\b(jpg|jpeg)\b", low) or any(term in raw for term in ("jpg格式", "jpeg格式")):
        return "jpg"
    if re.search(r"\bpng\b", low) or "png格式" in raw:
        return "png"
    return "png"


def _render_svg_to_png_with_cairosvg(svg: str) -> tuple:
    try:
        import cairosvg
    except Exception as exc:
        return None, f"cairosvg unavailable: {exc}"

    out = io.BytesIO()
    try:
        cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            write_to=out,
            output_width=1024,
            output_height=1024,
        )
        return out.getvalue(), "cairosvg"
    except Exception as exc:
        return None, f"cairosvg failed: {exc}"


def _render_svg_to_png_with_playwright(svg: str) -> tuple:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return None, f"playwright unavailable: {exc}"

    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>html,body{margin:0;width:1024px;height:1024px;background:transparent;}"
        "svg{display:block;width:1024px;height:1024px;}</style></head><body>"
        f"{svg}</body></html>"
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1024, "height": 1024}, device_scale_factor=1)
                page.set_content(html, wait_until="load")
                png = page.locator("svg").first.screenshot(type="png", omit_background=True)
            finally:
                browser.close()
        return png, "playwright"
    except Exception as exc:
        return None, f"playwright failed: {exc}"


def _svg_to_png_bytes(svg: str) -> tuple:
    errors = []
    for renderer in (_render_svg_to_png_with_cairosvg, _render_svg_to_png_with_playwright):
        png, info = renderer(svg)
        if png:
            return png, info, ""
        errors.append(info)
    return None, "", "; ".join(errors)


def _png_to_jpg_bytes(png_bytes: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as img:
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.getchannel("A"))
        out = io.BytesIO()
        background.save(out, format="JPEG", quality=92, optimize=True, progressive=True)
        return out.getvalue()


def _store_generated_image(svg: str, fs, output_format: str) -> dict:
    requested = (output_format or "png").lower()
    if requested == "jpeg":
        requested = "jpg"
    if requested not in {"png", "jpg", "svg"}:
        requested = "png"

    result = {
        "requested_format": requested,
        "format": "svg",
        "rasterized": False,
        "renderer": "",
        "raster_error": "",
    }

    if requested in {"png", "jpg"}:
        png_bytes, renderer, raster_error = _svg_to_png_bytes(svg)
        if png_bytes:
            try:
                if requested == "jpg":
                    data = _png_to_jpg_bytes(png_bytes)
                    ext = "jpg"
                    content_type = "image/jpeg"
                else:
                    data = png_bytes
                    ext = "png"
                    content_type = "image/png"
                filename = f"generated-image-{uuid.uuid4().hex}.{ext}"
                fs.put(data, filename=filename, content_type=content_type)
                result.update({
                    "filename": filename,
                    "format": ext,
                    "content_type": content_type,
                    "rasterized": True,
                    "renderer": renderer,
                })
                return result
            except Exception as exc:
                raster_error = f"{raster_error}; raster save failed: {exc}".strip("; ")
        result["raster_error"] = raster_error or "No SVG rasterizer was available."

    filename = f"generated-image-{uuid.uuid4().hex}.svg"
    fs.put(svg.encode("utf-8"), filename=filename, content_type="image/svg+xml")
    result.update({
        "filename": filename,
        "format": "svg",
        "content_type": "image/svg+xml",
    })
    return result


def image_ready_text(
    user_lang: str,
    used_uploaded_reference: bool = False,
    image_format: str = "svg",
    requested_format: str = "",
    provider: str = "",
    used_previous_reference: bool = False,
) -> str:
    fmt = (image_format or "svg").upper()
    requested = (requested_format or image_format or "svg").upper()
    backend = provider or "local image model"
    fallback_note_zh = ""
    fallback_note_ms = ""
    fallback_note_en = ""
    if fmt == "SVG" and requested in {"PNG", "JPG", "JPEG"}:
        fallback_note_zh = " 当前环境暂时没有可用的 SVG 转 PNG/JPG 渲染器，所以先返回 SVG。"
        fallback_note_ms = " Persekitaran semasa belum mempunyai perender SVG ke PNG/JPG yang boleh digunakan, jadi saya pulangkan SVG dahulu."
        fallback_note_en = " This environment does not currently have an available SVG-to-PNG/JPG renderer, so I returned SVG for now."

    if user_lang == "Chinese":
        ref = "，并参考了上一张生成图片" if used_previous_reference else ("，并参考了你上传的图片" if used_uploaded_reference else "")
        source = f"由 {backend} 本地生成" if provider else "由本地图片生成模型生成"
        return f"已生成一张 {fmt} 图片{ref}，{source}。{fallback_note_zh}"
    if user_lang == "Malay":
        ref = " berdasarkan imej jana sebelumnya" if used_previous_reference else (" berdasarkan imej yang anda muat naik" if used_uploaded_reference else "")
        return f"Saya sudah menjana imej {fmt}{ref} menggunakan {backend} secara tempatan.{fallback_note_ms}"
    ref = " using the previous generated image as reference" if used_previous_reference else (" using the uploaded image as reference" if used_uploaded_reference else "")
    return f"Generated a {fmt} image{ref} locally with {backend}.{fallback_note_en}"


def generate_image_with_model(
    prompt: str,
    attachments: list,
    *,
    fs,
    tokenizer,
    model_type,
    ollama_client,
    fast_model: str,
    cfg,
    fallback_generate=None,
    output_format: str = "",
) -> dict:
    encoded_images = encoded_images_from_attachments(attachments, fs)
    safe_prompt = (prompt or "").strip()[:3000]
    system_prompt = (
        "You are an expert SVG illustrator. Generate exactly one complete, safe SVG image.\n"
        "Return ONLY SVG markup, no markdown fence and no explanation.\n"
        "Do not call tools. Do not return JSON. Do not return actions such as dalle.text2im.\n"
        "Rules: 1024x1024 viewBox, polished vector style, readable composition, no external images, "
        "no scripts, no animation, no HTML, no links, no CSS style blocks. Use only basic SVG shapes, "
        "paths, gradients, and text when necessary."
    )
    user_prompt = (
        f"Create an SVG image for this request:\n{safe_prompt}\n\n"
        "If the user asks for photorealism, approximate it as a clean vector illustration."
    )
    gen_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    if encoded_images and (tokenizer == "ollama" or model_type in ("gguf", "ollama")):
        gen_messages[-1]["images"] = encoded_images[:3]

    def _call_model(messages_for_image: list, temperature: float = 0.78) -> str:
        if tokenizer == "ollama" or model_type in ("gguf", "ollama"):
            response = ollama_client.chat(
                model=fast_model,
                messages=messages_for_image,
                stream=False,
                think=False,
                options={
                    "temperature": temperature,
                    "top_p": 0.92,
                    "repeat_penalty": 1.05,
                    "num_predict": 4096,
                    "num_ctx": min(cfg.ollama_num_ctx_cap, 8192),
                    "num_gpu": cfg.ollama_num_gpu,
                    "num_thread": cfg.ollama_num_thread,
                    "use_mmap": True,
                },
                keep_alive="45m",
            )
            msg = response.get("message") if isinstance(response, dict) else getattr(response, "message", None)
            return msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        if fallback_generate:
            return fallback_generate(messages_for_image)
        raise RuntimeError("No image generation backend is available for this model path.")

    raw_svg = _call_model(gen_messages)

    svg = _sanitize_svg(raw_svg)
    if not svg:
        tool_prompt = _extract_prompt_from_tool_call(raw_svg)
        retry_prompt = (tool_prompt or safe_prompt).strip()
        retry_messages = [
            {
                "role": "system",
                "content": (
                    "Tools are disabled. You must draw the requested image yourself as SVG markup.\n"
                    "Return exactly one <svg>...</svg> document. No JSON, no markdown, no explanation, no action calls."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Convert this visual brief into a polished 1024x1024 SVG image:\n{retry_prompt}\n\n"
                    "Use a white or clean background, strong composition, and basic SVG shapes only."
                ),
            },
        ]
        if encoded_images and (tokenizer == "ollama" or model_type in ("gguf", "ollama")):
            retry_messages[-1]["images"] = encoded_images[:3]
        try:
            svg = _sanitize_svg(_call_model(retry_messages, temperature=0.42))
        except Exception as exc:
            print(f"[IMAGE GEN] SVG retry failed: {exc}")

        if not svg:
            svg = _sanitize_svg(_fallback_svg_from_prompt(retry_prompt))
            print("[IMAGE GEN] Used local fallback SVG because the model did not return SVG markup.")

    if not svg:
        raise ValueError("The model did not return a valid safe SVG.")
    result = _store_generated_image(svg, fs, output_format or requested_image_format(prompt))
    result["used_uploaded_reference"] = bool(encoded_images)
    return result


def generate_svg_with_model(
    prompt: str,
    attachments: list,
    *,
    fs,
    tokenizer,
    model_type,
    ollama_client,
    fast_model: str,
    cfg,
    fallback_generate=None,
) -> tuple:
    result = generate_image_with_model(
        prompt,
        attachments,
        fs=fs,
        tokenizer=tokenizer,
        model_type=model_type,
        ollama_client=ollama_client,
        fast_model=fast_model,
        cfg=cfg,
        fallback_generate=fallback_generate,
        output_format="svg",
    )
    return result["filename"], result["used_uploaded_reference"]
