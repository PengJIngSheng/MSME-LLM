import io
import gc
import os
import random
import re
import subprocess
import threading
import time
import uuid
from collections import deque

from . import image_gemma4


MODEL_ID = "stabilityai/stable-diffusion-3.5-medium"
_PIPELINE = None
_PIPELINE_MODE = None
_PIPELINE_LOCK = threading.Lock()
_GENERATION_LOCK = threading.Lock()

COLOR_MAP = {
    "white": (255, 255, 255),
    "black": (8, 8, 10),
    "gold": (212, 175, 55),
    "golden": (212, 175, 55),
    "yellow": (250, 204, 21),
    "blue": (37, 99, 235),
    "navy": (15, 23, 42),
    "red": (220, 38, 38),
    "green": (22, 163, 74),
    "gray": (229, 231, 235),
    "grey": (229, 231, 235),
    "金色": (212, 175, 55),
    "黄金色": (212, 175, 55),
    "白色": (255, 255, 255),
    "黑色": (8, 8, 10),
    "蓝色": (37, 99, 235),
    "深蓝色": (15, 23, 42),
    "红色": (220, 38, 38),
    "绿色": (22, 163, 74),
    "灰色": (229, 231, 235),
}


class SD35MediumError(RuntimeError):
    pass


ZH_TO_EN_TERMS = (
    ("生成一张", ""),
    ("生成一个", ""),
    ("PNG 图片", ""),
    ("JPG 图片", ""),
    ("图片", "image"),
    ("马来西亚", "Malaysia"),
    ("中小企业", "small business"),
    ("老板", "business owner"),
    ("电脑", "computer"),
    ("销售仪表盘", "sales analytics dashboard"),
    ("销售", "sales"),
    ("仪表盘", "analytics dashboard"),
    ("明亮办公室", "bright modern office"),
    ("办公室", "office"),
    ("现代商业插画风", "modern business illustration style"),
    ("商业插画", "business illustration"),
    ("蓝色和白色配色", "blue and white color palette"),
    ("蓝色", "blue"),
    ("白色", "white"),
    ("科技风", "technology style"),
    ("现代", "modern"),
    ("简洁", "clean minimal"),
    ("专业", "professional"),
    ("白色背景", "white background"),
    ("黑色", "black"),
    ("点缀", "accent"),
    ("海报", "poster"),
    ("图标", "icon"),
    ("标志", "logo"),
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _has_hf_token() -> bool:
    if os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN"):
        return True
    try:
        from huggingface_hub import get_token

        return bool(get_token())
    except Exception:
        return False


def _contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _rough_chinese_to_english(text: str) -> str:
    converted = text or ""
    for zh, en in ZH_TO_EN_TERMS:
        converted = converted.replace(zh, f" {en} ")
    converted = re.sub(r"[\u4e00-\u9fff]+", " ", converted)
    converted = re.sub(r"\s+", " ", converted)
    converted = converted.replace("：", ":").replace("，", ",").replace("。", ".").strip(" ,:.-")
    return converted.strip()


def build_sd35_prompt(prompt: str) -> tuple:
    raw = (prompt or "").strip()
    if _contains_chinese(raw):
        core = _rough_chinese_to_english(raw)
    else:
        core = raw

    lower = core.lower()
    if "dashboard" in lower or "business" in lower or "office" in lower:
        style = (
            "Malaysian small business owner at desk using laptop, large monitor showing blue sales analytics dashboard, "
            "bright modern office, clean SaaS business illustration, blue and white palette, professional website hero"
        )
    elif "logo" in lower or "icon" in lower:
        style = (
            "modern minimal technology logo, clean geometric brand mark, white background, "
            "black and electric blue accents, professional SaaS identity, centered"
        )
    else:
        style = (
            "modern clean digital illustration, professional commercial design, blue and white palette, sharp focus"
        )

    positive = (
        f"{style}. {core}. no text, no handwriting, no old paper"
    )
    negative = (
        "calligraphy, handwriting, Chinese characters, manuscript, old paper, book page, document scan, "
        "receipt, invoice, newspaper, random text, unreadable text, watermark, signature, seal stamp, "
        "low quality, blurry, distorted, deformed, extra fingers, bad anatomy"
    )
    return positive[:2000], negative


def _load_pipeline(mode: str = "txt2img"):
    global _PIPELINE, _PIPELINE_MODE
    with _PIPELINE_LOCK:
        if _PIPELINE is not None and _PIPELINE_MODE == mode:
            return _PIPELINE
        if _PIPELINE is not None and _PIPELINE_MODE != mode:
            _PIPELINE = None
            _PIPELINE_MODE = None
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

        if not _has_hf_token():
            raise SD35MediumError(
                "Stable Diffusion 3.5 Medium is a gated Hugging Face model. "
                "Accept the license on Hugging Face, then login with `huggingface-cli login` "
                "or set HF_TOKEN before restarting the server."
            )

        try:
            import torch
            from diffusers import StableDiffusion3Img2ImgPipeline, StableDiffusion3Pipeline
        except Exception as exc:
            raise SD35MediumError(
                "Missing SD3.5 runtime dependencies. Install diffusers, transformers, accelerate, and torch."
            ) from exc

        model_id = os.getenv("SD35_MODEL_ID", MODEL_ID)
        disable_t5 = os.getenv("SD35_DISABLE_T5", "0").strip().lower() not in {"0", "false", "no", "off"}
        dtype_name = os.getenv("SD35_DTYPE", "bfloat16").strip().lower()
        dtype = torch.float16 if dtype_name in {"fp16", "float16"} else torch.bfloat16

        kwargs = {"torch_dtype": dtype}
        if disable_t5:
            # Optional light mode: saves several GB on disk and VRAM, but prompt quality is lower
            # than the full T5 setup.
            kwargs["text_encoder_3"] = None
            kwargs["tokenizer_3"] = None

        try:
            pipeline_cls = StableDiffusion3Img2ImgPipeline if mode == "img2img" else StableDiffusion3Pipeline
            pipe = pipeline_cls.from_pretrained(model_id, **kwargs)
        except Exception as exc:
            raise SD35MediumError(
                f"Failed to load {model_id}. If this is the first run, confirm the Hugging Face license "
                f"has been accepted and enough disk space is available. Original error: {exc}"
            ) from exc

        if not torch.cuda.is_available():
            raise SD35MediumError("CUDA is not available; SD3.5 Medium should run on the RTX 4090 GPU.")

        pipe = pipe.to("cuda")
        try:
            pipe.enable_attention_slicing()
        except Exception:
            pass
        _PIPELINE = pipe
        _PIPELINE_MODE = mode
        return _PIPELINE


def _should_unload_after_generate() -> bool:
    return _env_flag("SD35_UNLOAD_AFTER_GENERATE", True)


def _configured_ollama_models() -> list:
    raw = os.getenv("SD35_STOP_OLLAMA_MODELS", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]

    try:
        result = subprocess.run(
            ["ollama", "ps"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []

    models = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if parts and ":" in parts[0]:
            models.append(parts[0])
    return models


def _stop_ollama_models_for_vram():
    if not _env_flag("SD35_STOP_OLLAMA_BEFORE_GENERATE", True):
        return

    models = _configured_ollama_models()
    for name in models:
        try:
            subprocess.run(
                ["ollama", "stop", name],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            pass

    if not models:
        return

    deadline = time.time() + max(1, _env_int("SD35_OLLAMA_STOP_WAIT_SECONDS", 20))
    while time.time() < deadline:
        still_loaded = set(_configured_ollama_models())
        if not any(name in still_loaded for name in models):
            return
        time.sleep(0.5)


def _unload_pipeline():
    global _PIPELINE, _PIPELINE_MODE
    with _PIPELINE_LOCK:
        _PIPELINE = None
        _PIPELINE_MODE = None
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass


def _attachment_filename(att: dict) -> str:
    if att.get("saved_path"):
        return os.path.basename(str(att.get("saved_path")).replace("\\", "/"))
    original = att.get("original_name") or ""
    ext = os.path.splitext(original)[1]
    if att.get("file_id"):
        return f"{att.get('file_id')}{ext}"
    return original


def _load_reference_image(attachments: list, fs):
    image_atts = image_gemma4.image_attachments(attachments or [])
    if not image_atts:
        return None, ""

    image_atts = sorted(image_atts, key=lambda att: 0 if att.get("generated_reference") else 1)
    for att in image_atts:
        filename = _attachment_filename(att)
        if not filename:
            continue
        file_doc = fs.find_one({"filename": filename})
        if not file_doc:
            continue
        try:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(file_doc.read())) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    alpha = img.convert("RGBA").getchannel("A")
                    background.paste(img.convert("RGBA"), mask=alpha)
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                return img.copy(), filename
        except Exception as exc:
            print(f"[SD35 EDIT] Failed to read reference image {filename}: {exc}")
    return None, ""


def _parse_color_value(value: str):
    value = (value or "").strip().lower().strip(" .,!?:;，。！？：；")
    hex_match = re.match(r"#?([0-9a-f]{6})$", value)
    if hex_match:
        raw = hex_match.group(1)
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4)), f"#{raw}"
    if value in COLOR_MAP:
        return COLOR_MAP[value], value
    return None, ""


def _requested_background_color(prompt: str):
    raw = prompt or ""
    low = raw.lower()
    patterns = (
        r"background(?:\s+color)?\s+(?:to|into|as)\s+([#a-z0-9]+)",
        r"(?:change|modify|edit|update|adjust|replace|recolor|make|turn)\s+(?:the\s+)?background(?:\s+color)?\s+(?:to|into|as)?\s*([#a-z0-9]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, low, re.IGNORECASE)
        if match:
            color, label = _parse_color_value(match.group(1))
            if color:
                return color, label

    zh_match = re.search(r"背景(?:颜色)?[^，。,.]{0,12}(?:改成|换成|变成|设为|改为|换为)(金色|黄金色|白色|黑色|蓝色|深蓝色|红色|绿色|灰色)", raw)
    if zh_match:
        color, label = _parse_color_value(zh_match.group(1))
        if color:
            return color, label
    return None, ""


def _edge_connected_background_mask(rgb, candidate):
    height, width = candidate.shape
    visited = [[False] * width for _ in range(height)]
    q = deque()

    def _push(y, x):
        if candidate[y, x] and not visited[y][x]:
            visited[y][x] = True
            q.append((y, x))

    for x in range(width):
        _push(0, x)
        _push(height - 1, x)
    for y in range(height):
        _push(y, 0)
        _push(y, width - 1)

    while q:
        y, x = q.popleft()
        if y > 0:
            _push(y - 1, x)
        if y + 1 < height:
            _push(y + 1, x)
        if x > 0:
            _push(y, x - 1)
        if x + 1 < width:
            _push(y, x + 1)

    try:
        import numpy as np

        return np.array(visited, dtype=bool)
    except Exception:
        return visited


def _recolor_background_if_requested(prompt: str, image):
    target, label = _requested_background_color(prompt)
    if not target:
        return None, ""

    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except Exception:
        return None, ""

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    corner = max(8, min(24, min(width, height) // 18))
    samples = np.concatenate([
        rgb[:corner, :corner].reshape(-1, 3),
        rgb[:corner, -corner:].reshape(-1, 3),
        rgb[-corner:, :corner].reshape(-1, 3),
        rgb[-corner:, -corner:].reshape(-1, 3),
    ], axis=0)
    background = np.median(samples, axis=0)
    dist = np.linalg.norm(rgb.astype(np.float32) - background.astype(np.float32), axis=2)
    threshold = max(18, min(96, _env_int("SD35_BACKGROUND_COLOR_THRESHOLD", 46)))
    candidate = dist <= threshold
    mask = _edge_connected_background_mask(rgb, candidate)
    coverage = float(mask.mean()) if hasattr(mask, "mean") else 0.0
    if coverage < 0.04:
        return None, ""

    mask_img = Image.fromarray((mask.astype("uint8") * 255), mode="L").filter(ImageFilter.GaussianBlur(0.8))
    new_bg = Image.new("RGB", image.size, target)
    edited = Image.composite(new_bg, image.convert("RGB"), mask_img)
    return edited, label


def _edit_strength(prompt: str) -> float:
    default = 0.42
    if _requested_background_color(prompt)[0]:
        default = 0.28
    return max(0.05, min(_env_float("SD35_EDIT_STRENGTH", default), 0.95))


def _build_sd35_edit_prompt(prompt: str, reference_name: str) -> tuple:
    raw = (prompt or "").strip()
    core = _rough_chinese_to_english(raw) if _contains_chinese(raw) else raw
    positive = (
        "Edit the provided image while preserving the main subject, composition, logo shape, proportions, "
        f"and overall identity. Apply this exact requested change: {core}. "
        "Keep the result clean, professional, high quality, sharp, and visually coherent."
    )
    negative = (
        "different logo, different layout, changed subject, extra text, random text, watermark, signature, "
        "low quality, blurry, distorted, deformed, artifacts, noisy background"
    )
    if reference_name:
        positive += f" Reference image: {reference_name}."
    return positive[:2000], negative


def _clean_replacement_text(text: str) -> str:
    cleaned = (text or "").strip().strip("\"'`“”‘’ ")
    cleaned = re.split(
        r"\s+(?:and|but|while)\s+(?:keep|preserve|maintain|use)\b|[,，。.!?]\s*(?:keep|preserve|maintain|use)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = cleaned.strip().strip("\"'`“”‘’ .,!?:;，。！？：；")
    if cleaned and cleaned == cleaned.lower() and re.search(r"[a-z]", cleaned):
        cleaned = " ".join(part.capitalize() if part.isalpha() else part for part in cleaned.split())
    return cleaned[:80]


def _requested_text_replacement(prompt: str) -> str:
    raw = prompt or ""
    patterns = (
        r"(?:change|modify|edit|update|replace|set|make)\s+(?:the\s+)?(?:company\s+name|brand\s+name|name|wording|words?|text|title|label|typography)\s+(?:to|into|as)\s+(.+)",
        r"(?:company\s+name|brand\s+name|name|wording|words?|text|title|label)\s+(?:to|into|as)\s+(.+)",
        r"(?:rename|call)\s+(?:it|this|the\s+company|the\s+brand|the\s+logo)?\s*(?:to|as)\s+(.+)",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            cleaned = _clean_replacement_text(match.group(1))
            if cleaned:
                return cleaned

    zh_match = re.search(
        r"(?:公司名|公司名称|品牌名|品牌名称|名称|名字|文字|文本|标题)[^，。,.]{0,12}(?:改成|换成|变成|设为|改为|换为)([^，。,.]+)",
        raw,
    )
    if zh_match:
        return _clean_replacement_text(zh_match.group(1))
    return ""


def _requested_text_removal(prompt: str) -> str:
    raw = (prompt or "").strip()
    patterns = (
        r"\b(?:remove|remvoe|delete|erase|hide|clear|take\s+out|get\s+rid\s+of)\b\s+(?:the\s+)?(.+)",
        r"\b(?:no|without)\b\s+(?:the\s+)?(.+)",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            target = _clean_replacement_text(match.group(1))
            if target:
                return target

    zh_match = re.search(r"(?:去除|删除|移除|去掉|擦掉|不要)(?:掉|了)?([^，。,.]*)", raw)
    if zh_match:
        return _clean_replacement_text(zh_match.group(1) or "文字")
    return ""


def _is_generic_text_target(target: str) -> bool:
    low = (target or "").lower()
    generic_terms = (
        "text", "word", "words", "company name", "brand name", "name", "label", "title",
        "typography", "lettering", "letters", "文字", "文本", "公司名", "公司名称",
        "品牌名", "品牌名称", "名字", "名称", "标题",
    )
    return not low or any(term in low for term in generic_terms)


def _font_path() -> str:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ):
        if os.path.exists(path):
            return path
    return ""


def _dominant_background_color(rgb):
    try:
        import numpy as np

        height, width = rgb.shape[:2]
        corner = max(8, min(28, min(width, height) // 16))
        samples = np.concatenate([
            rgb[:corner, :corner].reshape(-1, 3),
            rgb[:corner, -corner:].reshape(-1, 3),
            rgb[-corner:, :corner].reshape(-1, 3),
            rgb[-corner:, -corner:].reshape(-1, 3),
        ], axis=0)
        return np.median(samples, axis=0)
    except Exception:
        return None


def _find_likely_text_bbox(image):
    try:
        import numpy as np
    except Exception:
        return None, None, None

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    background = _dominant_background_color(rgb)

    def _best_bbox_from_mask(mask, target_y=0.68, min_y_ratio=0.34):
        mask = mask.copy()
        mask[: int(height * min_y_ratio), :] = False
        mask[int(height * 0.92):, :] = False
        row_counts = mask.sum(axis=1)
        row_threshold = max(8, int(width * 0.01))
        active_rows = row_counts > row_threshold
        segments = []
        start = None
        for idx, active in enumerate(active_rows):
            if active and start is None:
                start = idx
            elif not active and start is not None:
                if idx - start >= 5:
                    segments.append((start, idx))
                start = None
        if start is not None and len(active_rows) - start >= 5:
            segments.append((start, len(active_rows)))

        best = None
        best_score = -1.0
        best_pixels = None
        for y1, y2 in segments:
            ys, xs = np.where(mask[y1:y2, :])
            if len(xs) < 20:
                continue
            x1 = int(xs.min())
            x2 = int(xs.max()) + 1
            box_y1 = int(y1 + ys.min())
            box_y2 = int(y1 + ys.max()) + 1
            bw = x2 - x1
            bh = box_y2 - box_y1
            if bw < width * 0.08 or bh < 12:
                continue
            aspect = bw / max(1, bh)
            if aspect < 1.8:
                continue
            cy = (box_y1 + box_y2) / 2 / height
            y_score = 1.0 - min(1.0, abs(cy - target_y) / 0.34)
            compact_score = min(1.0, 90 / max(1, bh))
            score = aspect * min(1.0, bw / (width * 0.48)) * (0.8 + y_score) * compact_score
            if score > best_score:
                best_score = score
                best = (x1, box_y1, x2, box_y2)
                best_pixels = rgb[box_y1:box_y2, x1:x2][mask[box_y1:box_y2, x1:x2]]
        return best, best_pixels

    luma = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2])
    near_white = (rgb[:, :, 0] > 242) & (rgb[:, :, 1] > 242) & (rgb[:, :, 2] > 242)
    bright_text_mask = (luma > 105) & ~near_white
    best, text_pixels = _best_bbox_from_mask(bright_text_mask, target_y=0.68, min_y_ratio=0.45)
    if best:
        return best, background, text_pixels

    if background is not None:
        dist = np.linalg.norm(rgb.astype(np.float32) - background.astype(np.float32), axis=2)
        foreground_mask = dist > max(28, min(95, _env_int("SD35_TEXT_FOREGROUND_THRESHOLD", 52)))
        best, text_pixels = _best_bbox_from_mask(foreground_mask, target_y=0.62, min_y_ratio=0.34)
        if best:
            return best, background, text_pixels

    return None, background, None


def _sample_background_near_box(rgb, box):
    try:
        import numpy as np

        height, width = rgb.shape[:2]
        x1, y1, x2, y2 = box
        pad = max(8, int((y2 - y1) * 0.7))
        strips = []
        if y1 - pad >= 0:
            strips.append(rgb[y1 - pad:y1, max(0, x1 - pad):min(width, x2 + pad)].reshape(-1, 3))
        if y2 + pad <= height:
            strips.append(rgb[y2:y2 + pad, max(0, x1 - pad):min(width, x2 + pad)].reshape(-1, 3))
        if x1 - pad >= 0:
            strips.append(rgb[max(0, y1 - pad):min(height, y2 + pad), x1 - pad:x1].reshape(-1, 3))
        if x2 + pad <= width:
            strips.append(rgb[max(0, y1 - pad):min(height, y2 + pad), x2:x2 + pad].reshape(-1, 3))
        if strips:
            samples = np.concatenate([s for s in strips if len(s)], axis=0)
            if len(samples):
                return tuple(int(v) for v in np.median(samples, axis=0))
    except Exception:
        pass
    bg = _dominant_background_color(rgb)
    if bg is not None:
        return tuple(int(v) for v in bg)
    return (10, 18, 32)


def _contrast_color(background):
    r, g, b = background
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return (18, 24, 38) if luminance > 150 else (245, 245, 245)


def _text_candidate_bboxes(image):
    try:
        import numpy as np
    except Exception:
        return []

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    luma = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2])
    near_white = (rgb[:, :, 0] > 244) & (rgb[:, :, 1] > 244) & (rgb[:, :, 2] > 244)
    background = _dominant_background_color(rgb)

    masks = []
    masks.append(("dark", (luma < 96) & ~near_white))
    masks.append(("bright", (luma > 105) & ~near_white))
    if background is not None:
        dist = np.linalg.norm(rgb.astype(np.float32) - background.astype(np.float32), axis=2)
        masks.append(("foreground", dist > max(30, min(96, _env_int("SD35_TEXT_FOREGROUND_THRESHOLD", 54)))))

    candidates = []

    def _segments_from_mask(mask, kind):
        work = mask.copy()
        work[: int(height * 0.18), :] = False
        work[int(height * 0.94):, :] = False
        row_counts = work.sum(axis=1)
        row_threshold = max(8, int(width * 0.009))
        active_rows = row_counts > row_threshold
        segments = []
        start = None
        for idx, active in enumerate(active_rows):
            if active and start is None:
                start = idx
            elif not active and start is not None:
                if idx - start >= 7:
                    segments.append((start, idx))
                start = None
        if start is not None and len(active_rows) - start >= 7:
            segments.append((start, len(active_rows)))

        for y1, y2 in segments:
            ys, xs = np.where(work[y1:y2, :])
            if len(xs) < 28:
                continue
            x1 = int(xs.min())
            x2 = int(xs.max()) + 1
            box_y1 = int(y1 + ys.min())
            box_y2 = int(y1 + ys.max()) + 1
            bw = x2 - x1
            bh = box_y2 - box_y1
            if bw < width * 0.08 or bh < 10 or bh > height * 0.22:
                continue
            aspect = bw / max(1, bh)
            if aspect < 1.7:
                continue
            cy = (box_y1 + box_y2) / 2 / height
            density = len(xs) / max(1, bw * bh)
            if density < 0.015:
                continue
            lower_score = 1.0 + max(0.0, cy - 0.42)
            compact_score = min(1.4, 110 / max(1, bh))
            score = aspect * min(1.6, bw / max(1, width * 0.35)) * lower_score * compact_score
            if kind == "dark" and cy > 0.45:
                score *= 1.35
            pixels = rgb[box_y1:box_y2, x1:x2][work[box_y1:box_y2, x1:x2]]
            candidates.append({
                "bbox": (x1, box_y1, x2, box_y2),
                "score": float(score),
                "pixels": pixels,
                "kind": kind,
            })

    for kind, mask in masks:
        _segments_from_mask(mask, kind)

    candidates.sort(key=lambda item: item["score"], reverse=True)
    deduped = []
    for cand in candidates:
        x1, y1, x2, y2 = cand["bbox"]
        area = max(1, (x2 - x1) * (y2 - y1))
        duplicate = False
        for kept in deduped:
            kx1, ky1, kx2, ky2 = kept["bbox"]
            ix1, iy1 = max(x1, kx1), max(y1, ky1)
            ix2, iy2 = min(x2, kx2), min(y2, ky2)
            overlap = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            kept_area = max(1, (kx2 - kx1) * (ky2 - ky1))
            if overlap / min(area, kept_area) > 0.6:
                duplicate = True
                break
        if not duplicate:
            deduped.append(cand)
    return deduped[:8]


def _expanded_text_box(box, image_size):
    width, height = image_size
    x1, y1, x2, y2 = box
    bw = x2 - x1
    bh = y2 - y1
    pad_x = max(14, int(bw * 0.08))
    pad_y = max(8, int(bh * 0.28))
    return (
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(width, x2 + pad_x),
        min(height, y2 + pad_y),
    )


def _paint_over_box(image, box):
    try:
        import numpy as np
        from PIL import ImageDraw, ImageFilter
    except Exception:
        return image

    edited = image.convert("RGB").copy()
    rgb = np.asarray(edited)
    fill_color = _sample_background_near_box(rgb, box)
    global_bg = _dominant_background_color(rgb)
    if global_bg is not None:
        global_bg = tuple(int(v) for v in global_bg)
        global_luma = 0.299 * global_bg[0] + 0.587 * global_bg[1] + 0.114 * global_bg[2]
        fill_luma = 0.299 * fill_color[0] + 0.587 * fill_color[1] + 0.114 * fill_color[2]
        if global_luma > 218 and box[1] > image.size[1] * 0.42 and fill_luma > 150:
            fill_color = global_bg
    patch = edited.crop(box)
    draw = ImageDraw.Draw(patch)
    draw.rectangle((0, 0, box[2] - box[0], box[3] - box[1]), fill=fill_color)
    patch = patch.filter(ImageFilter.GaussianBlur(0.35))
    edited.paste(patch, box)
    return edited


def _remove_text_if_requested(prompt: str, image):
    target = _requested_text_removal(prompt)
    if not target:
        return None, ""

    candidates = _text_candidate_bboxes(image)
    if not candidates:
        return None, ""

    remove_all = bool(re.search(r"\b(all|every|everything)\b", target.lower())) or any(
        term in target for term in ("全部", "所有")
    )
    generic = _is_generic_text_target(target)
    if remove_all or generic:
        top_score = candidates[0]["score"]
        selected = [item for item in candidates if item["score"] >= top_score * 0.5][:4]
    else:
        # For a named text target such as "NexaMind", the most visually dominant
        # text line is usually the intended removal target.
        selected = [max(candidates, key=lambda item: (item["bbox"][2] - item["bbox"][0]) * (item["bbox"][3] - item["bbox"][1]))]

    edited = image.convert("RGB").copy()
    for cand in selected:
        edited = _paint_over_box(edited, _expanded_text_box(cand["bbox"], edited.size))
    return edited, target


def _edit_text_if_requested(prompt: str, image):
    replacement = _requested_text_replacement(prompt)
    if not replacement:
        return None, ""

    try:
        import numpy as np
        from PIL import ImageDraw, ImageFont
    except Exception:
        return None, ""

    edited = image.convert("RGB").copy()
    rgb = np.asarray(edited)
    height, width = edited.size[1], edited.size[0]
    bbox, dominant_bg, text_pixels = _find_likely_text_bbox(edited)
    if not bbox:
        bbox = (int(width * 0.24), int(height * 0.43), int(width * 0.76), int(height * 0.55))
        text_pixels = None

    x1, y1, x2, y2 = bbox
    pad_x = max(18, int((x2 - x1) * 0.14))
    pad_y = max(12, int((y2 - y1) * 0.72))
    box = (
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(width, x2 + pad_x),
        min(height, y2 + pad_y),
    )
    fill_color = _sample_background_near_box(rgb, box)

    if text_pixels is not None and len(text_pixels):
        text_color = tuple(int(v) for v in np.median(text_pixels, axis=0))
    else:
        text_color = _contrast_color(fill_color)
    if sum(abs(a - b) for a, b in zip(text_color, fill_color)) < 80:
        text_color = _contrast_color(fill_color)

    draw = ImageDraw.Draw(edited)
    draw.rectangle(box, fill=fill_color)

    font_file = _font_path()
    box_w = box[2] - box[0]
    box_h = box[3] - box[1]
    font_size = max(14, int(box_h * 0.58))
    font = ImageFont.truetype(font_file, font_size) if font_file else ImageFont.load_default()
    while font_size > 10:
        text_box = draw.textbbox((0, 0), replacement, font=font)
        text_w = text_box[2] - text_box[0]
        text_h = text_box[3] - text_box[1]
        if text_w <= box_w * 0.92 and text_h <= box_h * 0.74:
            break
        font_size -= 2
        font = ImageFont.truetype(font_file, font_size) if font_file else ImageFont.load_default()

    text_box = draw.textbbox((0, 0), replacement, font=font)
    text_w = text_box[2] - text_box[0]
    text_h = text_box[3] - text_box[1]
    x = box[0] + (box_w - text_w) / 2 - text_box[0]
    y = box[1] + (box_h - text_h) / 2 - text_box[1]
    draw.text((x, y), replacement, fill=text_color, font=font)
    return edited, replacement


def generate_local_image(prompt: str, attachments: list, *, fs) -> dict:
    safe_prompt = (prompt or "").strip()
    if not safe_prompt:
        raise SD35MediumError("Stable Diffusion needs a text prompt.")

    try:
        import torch
        from PIL import Image
    except Exception as exc:
        raise SD35MediumError("Missing torch or Pillow.") from exc

    width = max(512, min(_env_int("SD35_IMAGE_WIDTH", 768), 1536))
    height = max(512, min(_env_int("SD35_IMAGE_HEIGHT", 768), 1536))
    steps = max(4, min(_env_int("SD35_STEPS", 28), 80))
    guidance = _env_float("SD35_GUIDANCE_SCALE", 4.5)
    seed = _env_int("SD35_IMAGE_SEED", 0)
    if seed <= 0:
        seed = random.randint(1, 2**31 - 1)

    positive_prompt, negative_prompt = build_sd35_prompt(safe_prompt)
    generator = torch.Generator(device="cuda").manual_seed(seed)
    incoming_attachments = attachments or []
    reference_image, reference_name = _load_reference_image(incoming_attachments, fs)
    has_previous_reference = any(att.get("generated_reference") for att in incoming_attachments)
    is_edit = bool(reference_image and (has_previous_reference or image_gemma4.is_image_edit_request(safe_prompt)))
    used_previous_reference = bool(is_edit and has_previous_reference)
    provider = "Stable Diffusion 3.5 Medium"
    with _GENERATION_LOCK:
        if is_edit:
            handled_locally = False
            text_removed, removed_text = _remove_text_if_requested(safe_prompt, reference_image)
            if text_removed is not None:
                image = text_removed
                provider = f"local image editor (removed text: {removed_text})"
                handled_locally = True

            if not handled_locally:
                text_edited, replacement_text = _edit_text_if_requested(safe_prompt, reference_image)
                if text_edited is not None:
                    image = text_edited
                    provider = f"local image editor (text: {replacement_text})"
                    handled_locally = True

            if not handled_locally:
                recolored, color_label = _recolor_background_if_requested(safe_prompt, reference_image)
                if recolored is not None:
                    image = recolored
                    provider = f"local image editor ({color_label} background)"
                    handled_locally = True

            if not handled_locally:
                _stop_ollama_models_for_vram()
                pipe = _load_pipeline("img2img")
                edit_prompt, edit_negative = _build_sd35_edit_prompt(safe_prompt, reference_name)
                init_image = reference_image.resize((width, height))
                try:
                    image = pipe(
                        edit_prompt,
                        image=init_image,
                        negative_prompt=edit_negative,
                        strength=_edit_strength(safe_prompt),
                        width=width,
                        height=height,
                        num_inference_steps=steps,
                        guidance_scale=guidance,
                        generator=generator,
                    ).images[0]
                    provider = "Stable Diffusion 3.5 Medium img2img"
                except Exception as exc:
                    raise SD35MediumError(f"SD3.5 image edit failed: {exc}") from exc
                finally:
                    if _should_unload_after_generate():
                        try:
                            del pipe
                        except Exception:
                            pass
                        _unload_pipeline()
        else:
            _stop_ollama_models_for_vram()
            pipe = _load_pipeline("txt2img")
            try:
                image = pipe(
                    positive_prompt,
                    negative_prompt=negative_prompt,
                    width=width,
                    height=height,
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                    generator=generator,
                ).images[0]
            except Exception as exc:
                raise SD35MediumError(f"SD3.5 generation failed: {exc}") from exc
            finally:
                if _should_unload_after_generate():
                    try:
                        del pipe
                    except Exception:
                        pass
                    _unload_pipeline()

    requested = image_gemma4.requested_image_format(prompt)
    out = io.BytesIO()
    if requested == "jpg":
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(out, format="JPEG", quality=92, optimize=True, progressive=True)
        ext = "jpg"
        content_type = "image/jpeg"
    else:
        if not isinstance(image, Image.Image):
            raise SD35MediumError("SD3.5 did not return a PIL image.")
        image.save(out, format="PNG", optimize=True)
        ext = "png"
        content_type = "image/png"

    filename = f"sd35-medium-{uuid.uuid4().hex}.{ext}"
    fs.put(out.getvalue(), filename=filename, content_type=content_type)
    return {
        "filename": filename,
        "format": ext,
        "content_type": content_type,
        "provider": provider,
        "model": os.getenv("SD35_MODEL_ID", MODEL_ID),
        "requested_format": requested,
        "used_uploaded_reference": bool(reference_image),
        "used_previous_image_reference": used_previous_reference,
        "edited_reference_image": reference_name if is_edit else "",
        "seed": seed,
        "width": width,
        "height": height,
        "steps": steps,
    }
