"""
config_loader.py
================
Central configuration loader for Pepper Labs AI.

Priority order (highest wins):
  1. Environment variables  (e.g. $env:BING_API_KEY)
  2. config.yaml           (main config file)
  3. Built-in defaults     (fallback if config.yaml is missing)

Usage in any .py file:
  from config_loader import cfg
  print(cfg.think_model)
  print(cfg.max_new_tokens)
  print(cfg.bing_api_key)
"""

import os
import yaml
from pathlib import Path

# ── Locate config.yaml relative to this file ─────────────────────────────────
_CONFIG_PATH = Path(__file__).parent / "config.yaml"


# ── Built-in defaults (used when config.yaml is missing) ─────────────────────
_DEFAULTS = {
    "model": {
        "think_model":    "deepseek-r1-distill-qwen-14b",
        "fast_model":     "deepseek-r1-14b-fast",
        "gguf_path":      "./DeepSeek-R1-Distill-Qwen-14B-Q5_K_M.gguf",
        "quant_mode":     "4bit",
        "context_length": 8192,
    },
    "generation": {
        "max_new_tokens":    4096,
        "temperature":       0.65,
        "top_p":             0.95,
        "repetition_penalty": 1.05,
        "do_sample":         True,
        "default_think_mode": False,
    },
    "server": {
        "host":             "0.0.0.0",
        "port":             8000,
        "max_history_turns": 20,
        "ngrok": {
            "enabled":       False,
            "authtoken":     "",
            "domain":        "",
            "region":        "",
            "bind_tls":      True,
        },
    },
    "api_keys": {
        "bing_search": "",  # unused - DuckDuckGo only
    },
    "search": {
        "max_queries":           5,
        "max_results_per_query": 12,
        "max_results_total":     10,
        "snippet_length":        250,
        "blacklist_domains": [
            "kimi.ai", "gemini.google.com", "claude.ai", "openai.com",
            "anthropic.com", "copilot.microsoft.com", "chat.openai.com",
            "github.com", "stackoverflow.com", "youtube.com", "youtu.be",
            "twitter.com", "x.com", "facebook.com", "instagram.com",
            "linkedin.com", "tiktok.com", "pinterest.com",
            "google.com", "bing.com", "yahoo.com",
        ],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into `base`."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_raw() -> dict:
    """Load config.yaml and merge with defaults."""
    raw = {}
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            raw = loaded
            print(f"  📄 Config loaded: {_CONFIG_PATH}")
        except Exception as e:
            print(f"  ⚠️  Failed to load config.yaml: {e} — using defaults")
    else:
        print(f"  ℹ️  config.yaml not found at {_CONFIG_PATH} — using defaults")
    return _deep_merge(_DEFAULTS, raw)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _Config:
    """
    Flat-access config object with env-var override support.

    Example:
        cfg.think_model       → model.think_model
        cfg.max_new_tokens    → generation.max_new_tokens
        cfg.bing_api_key      → api_keys.bing_search  (or env BING_API_KEY)
        cfg.port              → server.port
        cfg.blacklist_domains → search.blacklist_domains
    """

    def __init__(self):
        self._raw = _load_raw()

    def reload(self):
        """Hot-reload config from disk (useful if you change config.yaml at runtime)."""
        self._raw = _load_raw()
        print("  🔄 Config reloaded.")

    # ── Model ────────────────────────────────────────────────────────────────
    @property
    def think_model(self) -> str:
        return self._raw["model"]["think_model"]

    @property
    def fast_model(self) -> str:
        return self._raw["model"]["fast_model"]

    @property
    def gguf_path(self) -> str:
        return self._raw["model"]["gguf_path"]

    @property
    def quant_mode(self) -> str:
        return self._raw["model"]["quant_mode"]

    @property
    def context_length(self) -> int:
        return int(self._raw["model"]["context_length"])

    # ── Generation ───────────────────────────────────────────────────────────
    @property
    def max_new_tokens(self) -> int:
        return int(self._raw["generation"]["max_new_tokens"])

    @max_new_tokens.setter
    def max_new_tokens(self, value: int):
        self._raw["generation"]["max_new_tokens"] = int(value)

    @property
    def temperature(self) -> float:
        return float(self._raw["generation"]["temperature"])

    @property
    def top_p(self) -> float:
        return float(self._raw["generation"]["top_p"])

    @property
    def repetition_penalty(self) -> float:
        return float(self._raw["generation"]["repetition_penalty"])

    @property
    def do_sample(self) -> bool:
        return bool(self._raw["generation"]["do_sample"])

    @property
    def default_think_mode(self) -> bool:
        return bool(self._raw["generation"]["default_think_mode"])

    # ── Server ───────────────────────────────────────────────────────────────
    @property
    def host(self) -> str:
        return self._raw["server"]["host"]

    @property
    def port(self) -> int:
        return int(self._raw["server"]["port"])

    @property
    def max_history_turns(self) -> int:
        return int(self._raw["server"]["max_history_turns"])

    @property
    def timezone(self) -> str:
        return self._raw["server"].get("timezone", "Asia/Kuala_Lumpur")

    @property
    def ngrok_enabled(self) -> bool:
        ngrok_cfg = self._raw["server"].get("ngrok", {})
        return _env_bool("NGROK_ENABLED", bool(ngrok_cfg.get("enabled", False)))

    @property
    def ngrok_authtoken(self) -> str:
        ngrok_cfg = self._raw["server"].get("ngrok", {})
        return os.getenv("NGROK_AUTHTOKEN", ngrok_cfg.get("authtoken", "") or "")

    @property
    def ngrok_domain(self) -> str:
        ngrok_cfg = self._raw["server"].get("ngrok", {})
        return os.getenv("NGROK_DOMAIN", ngrok_cfg.get("domain", "") or "")

    @property
    def ngrok_region(self) -> str:
        ngrok_cfg = self._raw["server"].get("ngrok", {})
        return os.getenv("NGROK_REGION", ngrok_cfg.get("region", "") or "")

    @property
    def ngrok_bind_tls(self) -> bool:
        ngrok_cfg = self._raw["server"].get("ngrok", {})
        return _env_bool("NGROK_BIND_TLS", bool(ngrok_cfg.get("bind_tls", True)))

    # ── API Keys (env vars take priority) ────────────────────────────────────
    @property
    def bing_api_key(self) -> str:
        """Bing API disabled — DuckDuckGo only. Returns empty string."""
        return ""

    # ── Search ───────────────────────────────────────────────────────────────
    @property
    def max_queries(self) -> int:
        return int(self._raw["search"]["max_queries"])

    @property
    def max_results_per_query(self) -> int:
        return int(self._raw["search"]["max_results_per_query"])

    @property
    def max_results_total(self) -> int:
        return int(self._raw["search"]["max_results_total"])

    @property
    def snippet_length(self) -> int:
        return int(self._raw["search"]["snippet_length"])

    @property
    def blacklist_domains(self) -> list:
        return list(self._raw["search"]["blacklist_domains"])

    # ── Debug summary ────────────────────────────────────────────────────────
    def print_summary(self):
        bing = "✅ SET" if self.bing_api_key else "❌ not set"
        print(f"""
  ┌─ Config Summary ──────────────────────────────────┐
  │  Think model   : {self.think_model}
  │  Fast model    : {self.fast_model}
  │  Context length: {self.context_length} tokens
  │  Max tokens    : {self.max_new_tokens}
  │  Temperature   : {self.temperature}
  │  Top-P         : {self.top_p}
  │  Repeat penalty: {self.repetition_penalty}
  │  Server        : {self.host}:{self.port}
  │  Bing API key  : {bing}
  │  Search queries: max {self.max_queries} queries × {self.max_results_per_query} results
  └───────────────────────────────────────────────────┘""")


# ── Singleton instance (import anywhere) ─────────────────────────────────────
cfg = _Config()
