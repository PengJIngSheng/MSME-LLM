import copy
import json
import mimetypes
import os
import random
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import image_gemma4


DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT_SECONDS = 240
DEFAULT_POLL_INTERVAL_SECONDS = 1.0


class LocalImageGenerationError(RuntimeError):
    pass


def is_enabled() -> bool:
    return str(os.getenv("COMFYUI_IMAGE_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def base_url() -> str:
    return (os.getenv("COMFYUI_BASE_URL") or DEFAULT_COMFYUI_URL).strip().rstrip("/")


def _request_json(method: str, url: str, payload: dict = None, timeout: int = 30) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LocalImageGenerationError(f"ComfyUI HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise LocalImageGenerationError(
            f"Cannot reach ComfyUI at {base_url()}. Start ComfyUI on port 8188 first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise LocalImageGenerationError(f"ComfyUI returned invalid JSON from {url}") from exc


def _request_bytes(url: str, timeout: int = 60) -> tuple:
    req = Request(url, headers={"Accept": "image/*"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as res:
            return res.read(), res.headers.get_content_type() or "image/png"
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LocalImageGenerationError(f"ComfyUI image download HTTP {exc.code}: {detail[:300]}") from exc
    except URLError as exc:
        raise LocalImageGenerationError(f"ComfyUI image download failed: {exc}") from exc


def _multipart_upload(url: str, field_name: str, filename: str, content_type: str, data: bytes) -> dict:
    boundary = f"----MSMEAI{uuid.uuid4().hex}"
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8")
    )
    body.extend(f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode("utf-8"))
    body.extend(data)
    body.extend(b"\r\n")
    add_field("type", "input")
    add_field("overwrite", "true")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    req = Request(
        url,
        data=bytes(body),
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=60) as res:
            raw = res.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LocalImageGenerationError(f"ComfyUI upload HTTP {exc.code}: {detail[:300]}") from exc
    except URLError as exc:
        raise LocalImageGenerationError(f"ComfyUI upload failed: {exc}") from exc


def _attachment_filename(att: dict) -> str:
    return att.get("saved_path") or (att.get("file_id", "") + os.path.splitext(att.get("original_name", ""))[1])


def _upload_reference_images(attachments: list, fs, comfy_url: str, max_images: int = 4) -> list:
    uploaded = []
    for att in image_gemma4.image_attachments(attachments or [])[:max_images]:
        filename = _attachment_filename(att)
        file_doc = fs.find_one({"filename": filename})
        if not file_doc:
            continue
        raw = file_doc.read()
        original = att.get("original_name") or filename or f"reference-{len(uploaded) + 1}.png"
        content_type = att.get("content_type") or mimetypes.guess_type(original)[0] or "image/png"
        safe_name = f"msme-ref-{uuid.uuid4().hex}{Path(original).suffix or '.png'}"
        result = _multipart_upload(f"{comfy_url}/upload/image", "image", safe_name, content_type, raw)
        uploaded_name = result.get("name") or result.get("filename") or safe_name
        uploaded.append(uploaded_name)
    return uploaded


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


def _default_flux_workflow(prompt: str, negative_prompt: str, seed: int) -> dict:
    width = _env_int("COMFYUI_IMAGE_WIDTH", 1024)
    height = _env_int("COMFYUI_IMAGE_HEIGHT", 1024)
    steps = _env_int("COMFYUI_IMAGE_STEPS", 28)
    cfg = _env_float("COMFYUI_IMAGE_CFG", 1.0)
    sampler = os.getenv("COMFYUI_SAMPLER", "euler")
    scheduler = os.getenv("COMFYUI_SCHEDULER", "simple")
    model_name = os.getenv("COMFYUI_FLUX_MODEL_NAME", "flux2-dev.safetensors")
    clip_l = os.getenv("COMFYUI_CLIP_L_NAME", "clip_l.safetensors")
    t5 = os.getenv("COMFYUI_T5_NAME", "t5xxl_fp16.safetensors")
    vae = os.getenv("COMFYUI_VAE_NAME", "ae.safetensors")
    prefix = os.getenv("COMFYUI_FILENAME_PREFIX", "MSMEAI_local")

    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": model_name, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "DualCLIPLoader",
            "inputs": {"clip_name1": clip_l, "clip_name2": t5, "type": "flux", "device": "default"},
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": vae},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["2", 0]},
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["2", 0]},
        },
        "6": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["7", 0], "vae": ["3", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prefix, "images": ["8", 0]},
        },
    }


def _load_workflow(prompt: str, negative_prompt: str, seed: int) -> dict:
    workflow_path = (os.getenv("COMFYUI_WORKFLOW_JSON") or "").strip()
    if workflow_path:
        path = Path(workflow_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise LocalImageGenerationError(f"COMFYUI_WORKFLOW_JSON does not exist: {path}")
        with path.open("r", encoding="utf-8") as f:
            workflow = json.load(f)
        if isinstance(workflow, dict) and isinstance(workflow.get("prompt"), dict):
            workflow = workflow["prompt"]
    else:
        workflow = _default_flux_workflow(prompt, negative_prompt, seed)
    return _patch_workflow(workflow, prompt, negative_prompt, seed)


def _sorted_nodes(workflow: dict) -> list:
    def key(item):
        node_id, _ = item
        return (0, int(node_id)) if str(node_id).isdigit() else (1, str(node_id))

    return sorted(workflow.items(), key=key)


def _patch_workflow(workflow: dict, prompt: str, negative_prompt: str, seed: int) -> dict:
    workflow = copy.deepcopy(workflow)
    text_nodes_seen = 0
    width = _env_int("COMFYUI_IMAGE_WIDTH", 1024)
    height = _env_int("COMFYUI_IMAGE_HEIGHT", 1024)
    steps = _env_int("COMFYUI_IMAGE_STEPS", 28)
    cfg = _env_float("COMFYUI_IMAGE_CFG", 1.0)
    sampler = os.getenv("COMFYUI_SAMPLER", "")
    scheduler = os.getenv("COMFYUI_SCHEDULER", "")
    prefix = os.getenv("COMFYUI_FILENAME_PREFIX", "MSMEAI_local")

    for _, node in _sorted_nodes(workflow):
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = str(node.get("class_type", "")).lower()

        if "text" in inputs and ("cliptextencode" in class_type or "prompt" in class_type or "text" in class_type):
            if text_nodes_seen == 0:
                inputs["text"] = prompt
            elif text_nodes_seen == 1:
                inputs["text"] = negative_prompt
            text_nodes_seen += 1
        if "positive_prompt" in inputs:
            inputs["positive_prompt"] = prompt
        if "negative_prompt" in inputs:
            inputs["negative_prompt"] = negative_prompt
        if "prompt" in inputs and isinstance(inputs.get("prompt"), str):
            inputs["prompt"] = prompt

        if "seed" in inputs and isinstance(inputs.get("seed"), int):
            inputs["seed"] = seed
        if "noise_seed" in inputs and isinstance(inputs.get("noise_seed"), int):
            inputs["noise_seed"] = seed
        if "width" in inputs and isinstance(inputs.get("width"), int):
            inputs["width"] = width
        if "height" in inputs and isinstance(inputs.get("height"), int):
            inputs["height"] = height
        if "steps" in inputs and isinstance(inputs.get("steps"), int):
            inputs["steps"] = steps
        if "cfg" in inputs and isinstance(inputs.get("cfg"), (int, float)):
            inputs["cfg"] = cfg
        if sampler and "sampler_name" in inputs:
            inputs["sampler_name"] = sampler
        if scheduler and "scheduler" in inputs:
            inputs["scheduler"] = scheduler
        if "filename_prefix" in inputs:
            inputs["filename_prefix"] = prefix

    return workflow


def _inject_reference_images(workflow: dict, uploaded_names: list) -> dict:
    if not uploaded_names:
        return workflow
    workflow = copy.deepcopy(workflow)
    idx = 0
    for _, node in _sorted_nodes(workflow):
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = str(node.get("class_type", "")).lower()
        if "loadimage" in class_type and "image" in inputs:
            inputs["image"] = uploaded_names[min(idx, len(uploaded_names) - 1)]
            idx += 1
    return workflow


def _queue_prompt(workflow: dict, comfy_url: str) -> str:
    client_id = str(uuid.uuid4())
    response = _request_json("POST", f"{comfy_url}/prompt", {"prompt": workflow, "client_id": client_id}, timeout=30)
    prompt_id = response.get("prompt_id")
    if not prompt_id:
        raise LocalImageGenerationError(f"ComfyUI did not return prompt_id: {response}")
    return prompt_id


def _wait_for_images(prompt_id: str, comfy_url: str, timeout_seconds: int, poll_interval: float) -> list:
    deadline = time.monotonic() + max(10, timeout_seconds)
    last_history = {}
    while time.monotonic() < deadline:
        time.sleep(max(0.25, poll_interval))
        history = _request_json("GET", f"{comfy_url}/history/{prompt_id}", timeout=30)
        last_history = history
        item = history.get(prompt_id)
        if not item:
            continue
        if item.get("status", {}).get("status_str") == "error":
            raise LocalImageGenerationError(f"ComfyUI workflow failed: {item.get('status')}")
        outputs = item.get("outputs") or {}
        images = []
        for output in outputs.values():
            for image in output.get("images", []) if isinstance(output, dict) else []:
                if image.get("filename"):
                    images.append(image)
        if images:
            return images
    raise LocalImageGenerationError(f"ComfyUI generation timed out. Last history: {str(last_history)[:500]}")


def _save_comfy_image_to_gridfs(image_meta: dict, comfy_url: str, fs) -> dict:
    query = urlencode({
        "filename": image_meta.get("filename", ""),
        "subfolder": image_meta.get("subfolder", ""),
        "type": image_meta.get("type", "output"),
    })
    data, content_type = _request_bytes(f"{comfy_url}/view?{query}", timeout=60)
    ext = "png"
    if "jpeg" in content_type or "jpg" in content_type:
        ext = "jpg"
    elif "webp" in content_type:
        ext = "webp"
    filename = f"local-image-{uuid.uuid4().hex}.{ext}"
    fs.put(data, filename=filename, content_type=content_type)
    return {"filename": filename, "format": ext, "content_type": content_type}


def generate_local_image(prompt: str, attachments: list, *, fs) -> dict:
    if not is_enabled():
        raise LocalImageGenerationError("Local ComfyUI image generation is disabled by COMFYUI_IMAGE_ENABLED=0.")

    comfy_url = base_url()
    seed = _env_int("COMFYUI_IMAGE_SEED", 0)
    if seed <= 0:
        seed = random.randint(1, 2**31 - 1)
    timeout_seconds = _env_int("COMFYUI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    poll_interval = _env_float("COMFYUI_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
    negative_prompt = os.getenv(
        "COMFYUI_NEGATIVE_PROMPT",
        "low quality, blurry, distorted anatomy, unreadable text, watermark, extra fingers",
    )

    uploaded_names = _upload_reference_images(attachments or [], fs, comfy_url)
    workflow = _load_workflow((prompt or "").strip(), negative_prompt, seed)
    workflow = _inject_reference_images(workflow, uploaded_names)
    prompt_id = _queue_prompt(workflow, comfy_url)
    images = _wait_for_images(prompt_id, comfy_url, timeout_seconds, poll_interval)
    saved = _save_comfy_image_to_gridfs(images[0], comfy_url, fs)
    saved.update({
        "provider": "ComfyUI",
        "model": os.getenv("COMFYUI_MODEL_LABEL", os.getenv("COMFYUI_FLUX_MODEL_NAME", "local-image-model")),
        "requested_format": image_gemma4.requested_image_format(prompt),
        "used_uploaded_reference": bool(uploaded_names),
        "seed": seed,
        "prompt_id": prompt_id,
    })
    return saved
