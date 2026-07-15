# Local Image Generation

This folder owns MSME.AI image understanding and local image generation.

The recommended local setup for a 48GB RTX 4090 is:

- ComfyUI as the local image server
- FLUX.2 dev as the main high-quality generation workflow
- Qwen-Image / Qwen-Image-Edit as optional workflows for text-heavy posters, logos, and image editing

The app talks to ComfyUI at `http://127.0.0.1:8188` and stores the generated PNG/JPG in MongoDB GridFS.

## Run ComfyUI

Install ComfyUI outside this repo, then start it locally:

```bash
python main.py --listen 127.0.0.1 --port 8188
```

## Basic Environment

Set these before starting `server.py`:

```bash
export COMFYUI_IMAGE_ENABLED=1
export COMFYUI_BASE_URL=http://127.0.0.1:8188
export COMFYUI_TIMEOUT_SECONDS=240
```

## Best Mode: Exported API Workflow

The safest production path is to build a working FLUX.2 or Qwen-Image workflow inside ComfyUI, enable developer mode, then export it as API JSON.

Then point MSME.AI at it:

```bash
export COMFYUI_WORKFLOW_JSON=/home/ubuntu/MSME-LLM/ImageGemma4/comfyui_workflows/flux2_dev_api.json
```

The provider automatically patches common workflow inputs:

- positive prompt
- negative prompt
- seed
- width / height
- steps
- cfg
- sampler / scheduler
- filename prefix
- `LoadImage` nodes for uploaded reference images

## Built-In Fallback Workflow

If `COMFYUI_WORKFLOW_JSON` is not set, MSME.AI sends a simple FLUX-style workflow using standard ComfyUI nodes:

- `UNETLoader`
- `DualCLIPLoader`
- `VAELoader`
- `CLIPTextEncode`
- `EmptyLatentImage`
- `KSampler`
- `VAEDecode`
- `SaveImage`

Default model names:

```bash
export COMFYUI_FLUX_MODEL_NAME=flux2-dev.safetensors
export COMFYUI_CLIP_L_NAME=clip_l.safetensors
export COMFYUI_T5_NAME=t5xxl_fp16.safetensors
export COMFYUI_VAE_NAME=ae.safetensors
```

If your ComfyUI model filenames are different, update those env vars.

## Generation Controls

```bash
export COMFYUI_IMAGE_WIDTH=1024
export COMFYUI_IMAGE_HEIGHT=1024
export COMFYUI_IMAGE_STEPS=28
export COMFYUI_IMAGE_CFG=1.0
export COMFYUI_SAMPLER=euler
export COMFYUI_SCHEDULER=simple
export COMFYUI_FILENAME_PREFIX=MSMEAI_local
export COMFYUI_MODEL_LABEL="FLUX.2 dev"
```

Use `COMFYUI_IMAGE_SEED=0` for random seeds, or set a fixed seed for reproducible tests.

## Direct Stable Diffusion 3.5 Medium Mode

This mode does not need ComfyUI. It loads SD3.5 Medium directly with `diffusers`.

SD3.5 Medium is gated on Hugging Face, so first accept the model license on:

```text
https://huggingface.co/stabilityai/stable-diffusion-3.5-medium
```

Then login or set a token:

```bash
huggingface-cli login
# or
export HF_TOKEN=hf_...
```

Start with the full T5 text encoder for better prompt understanding:

```bash
# SD3.5 is now the default local provider in server.py.
export LOCAL_IMAGE_PROVIDER=sd35
export SD35_DISABLE_T5=0
export SD35_IMAGE_WIDTH=768
export SD35_IMAGE_HEIGHT=768
export SD35_STEPS=28
export SD35_GUIDANCE_SCALE=4.5
export SD35_STOP_OLLAMA_BEFORE_GENERATE=1
export SD35_UNLOAD_AFTER_GENERATE=1
```

If disk or VRAM becomes tight, you can temporarily return to light mode:

```bash
export SD35_DISABLE_T5=1
```

Test one image before using the web app:

```bash
python ImageGemma4/sd35_medium_test.py "A modern MSME.AI logo, white background, black and electric blue accents"
```

The first full-T5 run downloads the extra `text_encoder_3` files. Expect roughly 9GB more cache than light mode. Add `--light` to the test command if you need the smaller setup.
