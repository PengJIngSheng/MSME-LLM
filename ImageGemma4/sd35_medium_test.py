import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _disable_t5_from_env() -> bool:
    return os.getenv("SD35_DISABLE_T5", "0").strip().lower() not in {"0", "false", "no", "off"}


def main():
    parser = argparse.ArgumentParser(description="Generate one local SD3.5 Medium test image.")
    parser.add_argument("prompt", nargs="?", default="A modern MSME.AI logo, white background, black and electric blue accents")
    parser.add_argument("--out", default="ImageGemma4/sd35_medium_test.png")
    parser.add_argument("--width", type=int, default=int(os.getenv("SD35_IMAGE_WIDTH", "768")))
    parser.add_argument("--height", type=int, default=int(os.getenv("SD35_IMAGE_HEIGHT", "768")))
    parser.add_argument("--steps", type=int, default=int(os.getenv("SD35_STEPS", "28")))
    parser.add_argument("--guidance", type=float, default=float(os.getenv("SD35_GUIDANCE_SCALE", "4.5")))
    parser.add_argument("--seed", type=int, default=int(os.getenv("SD35_IMAGE_SEED", "12345")))
    parser.add_argument("--full-t5", action="store_true", help="Force the full T5 text encoder.")
    parser.add_argument("--light", action="store_true", help="Disable the large T5 text encoder to save disk/VRAM.")
    args = parser.parse_args()

    from huggingface_hub import get_token

    if not (os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN") or get_token()):
        raise SystemExit(
            "Missing HF_TOKEN. Accept the SD3.5 Medium license on Hugging Face, then run `huggingface-cli login` "
            "or export HF_TOKEN before running this script."
        )

    import torch
    from diffusers import StableDiffusion3Pipeline
    from ImageGemma4.sd35_medium_local import build_sd35_prompt

    disable_t5 = args.light or (not args.full_t5 and _disable_t5_from_env())

    kwargs = {"torch_dtype": torch.bfloat16}
    if disable_t5:
        kwargs["text_encoder_3"] = None
        kwargs["tokenizer_3"] = None

    pipe = StableDiffusion3Pipeline.from_pretrained(
        os.getenv("SD35_MODEL_ID", "stabilityai/stable-diffusion-3.5-medium"),
        **kwargs,
    ).to("cuda")
    try:
        pipe.enable_attention_slicing()
    except Exception:
        pass

    generator = torch.Generator(device="cuda").manual_seed(args.seed)
    positive_prompt, negative_prompt = build_sd35_prompt(args.prompt)
    image = pipe(
        positive_prompt,
        negative_prompt=negative_prompt,
        width=args.width,
        height=args.height,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance,
        generator=generator,
    ).images[0]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    print(out)


if __name__ == "__main__":
    main()
