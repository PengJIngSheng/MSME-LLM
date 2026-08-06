"""
Central profile-aware configuration loader for bisnes.ai.

Load order, lowest to highest priority:
  1. Built-in defaults
  2. config/default.yaml
  3. legacy config.yaml
  4. config/<APP_PROFILE>.yaml
  5. CONFIG_FILE, if set
  6. environment variables and ignored secrets env files

Secrets env files are loaded before YAML values are exposed:
  .env
  config/secrets.env
  config/secrets.<APP_PROFILE>.env
  APP_SECRETS_FILE
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = ROOT_DIR / "config"
LEGACY_CONFIG_PATH = ROOT_DIR / "config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in (override or {}).items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(value: str | None) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((ROOT_DIR / path).resolve())


_DEFAULTS = {
    "runtime": {
        "profile": "local.windows",
        "name": "bisnes.ai local development",
    },
    "model": {
        "think_model": "deepseek-r1-distill-qwen-14b",
        "fast_model": "deepseek-r1-14b-fast",
        "search_query_model": "",
        "gguf_path": "./DeepSeek-R1-Distill-Qwen-14B-Q5_K_M.gguf",
        "quant_mode": "4bit",
        "context_length": 8192,
    },
    "generation": {
        "max_new_tokens": 4096,
        "temperature": 0.65,
        "top_p": 0.95,
        "repetition_penalty": 1.05,
        "do_sample": True,
        "default_think_mode": False,
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "timezone": "Asia/Shanghai",
        "public_site_url": "http://localhost:8000",
        "max_history_turns": 20,
    },
    "database": {
        "mongodb": {
            "uri": "mongodb://localhost:27017/",
            "database": "pepper_chat_db",
        },
        "pgvector": {
            "connection_uri": "",
            "collection": "users_memory",
        },
    },
    "services": {
        "ollama": {
            "base_url": "http://localhost:11434",
            "embedding_model": "nomic-embed-text",
            "num_ctx_cap": 8192,
            "num_gpu": 99,
            "num_thread": 4,
        },
        "smtp": {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "",
            "app_password": "",
            "from_name": "bisnes.ai",
        },
        "google": {
            "login_oauth_client_id": "",
            "oauth_client_id": "589970414306-pgibl250mccaj3qohvt3mhp3e0qsuj75.apps.googleusercontent.com",
            "oauth_client_secret": "",
            "client_secret_file": "secrets/google_client_secret.json",
        },
    },
    "security": {
        "jwt_secret": "",
        "google_token_encryption_key": "",
        "jwt_algorithm": "HS256",
        "jwt_expiration_hours": 24,
    },
    "deployment": {
        "ssh": {
            "host": "",
            "port": 22,
            "user": "",
            "identity_file": "",
        },
        "docker": {
            "mongo_port": 27017,
            "pgvector_port": 5432,
            "ollama_port": 11434,
            "app_port": 8000,
        },
    },
    "search": {
        "max_queries": 5,
        "max_results_per_query": 12,
        "max_results_total": 10,
        "min_results_total": 5,
        "snippet_length": 250,
        "timeout_seconds": 2,
        "html_timeout_seconds": 1.5,
        "deadline_seconds": 5,
        "max_workers": 5,
        "blacklist_domains": [
            "kimi.ai", "gemini.google.com", "openai.com",
            "copilot.microsoft.com", "chat.openai.com",
            "github.com", "stackoverflow.com", "youtube.com", "youtu.be",
            "twitter.com", "x.com", "facebook.com", "instagram.com",
            "linkedin.com", "tiktok.com", "pinterest.com",
            "google.com", "bing.com", "yahoo.com",
        ],
    },
    "knowledge_rag": {
        "enabled": True,
        "collection": "mof_finetune_knowledge",
        "top_k": 5,
        "score_threshold": 0.37,
        "max_context_chars": 6500,
    },
}


class _Config:
    def __init__(self) -> None:
        _load_env_file(ROOT_DIR / ".env")
        self.profile = os.getenv("APP_PROFILE") or os.getenv("MOF_PROFILE") or "local.windows"
        self._load_secret_env_files()
        self._raw = self._load_raw()
        self._apply_runtime_env()

    def _load_secret_env_files(self) -> None:
        candidates = [
            CONFIG_DIR / "secrets.env",
            CONFIG_DIR / f"secrets.{self.profile}.env",
        ]
        custom = os.getenv("APP_SECRETS_FILE")
        if custom:
            candidates.append(Path(custom))
        for path in candidates:
            _load_env_file(path)

    def _load_raw(self) -> dict:
        raw = dict(_DEFAULTS)
        loaded_paths = []
        for path in [
            CONFIG_DIR / "default.yaml",
            LEGACY_CONFIG_PATH,
            CONFIG_DIR / f"{self.profile}.yaml",
        ]:
            data = _load_yaml(path)
            if data:
                raw = _deep_merge(raw, data)
                loaded_paths.append(str(path.relative_to(ROOT_DIR)))

        custom = os.getenv("CONFIG_FILE")
        if custom:
            custom_data = _load_yaml(Path(custom))
            if custom_data:
                raw = _deep_merge(raw, custom_data)
                loaded_paths.append(custom)

        raw.setdefault("runtime", {})["profile"] = self.profile
        self.loaded_paths = loaded_paths
        return raw

    def _apply_runtime_env(self) -> None:
        if self.ollama_base_url:
            os.environ.setdefault("OLLAMA_HOST", self.ollama_base_url)

    def reload(self) -> None:
        self._load_secret_env_files()
        self._raw = self._load_raw()
        self._apply_runtime_env()

    def get(self, dotted: str, default: Any = None) -> Any:
        current: Any = self._raw
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def _env(self, name: str, dotted: str, default: Any = None) -> Any:
        value = os.getenv(name)
        if value is not None:
            return value
        return self.get(dotted, default)

    # Model
    @property
    def think_model(self) -> str:
        return str(self._env("THINK_MODEL", "model.think_model"))

    @property
    def fast_model(self) -> str:
        return str(self._env("FAST_MODEL", "model.fast_model"))

    @property
    def search_query_model(self) -> str:
        configured = str(self._env("SEARCH_QUERY_MODEL", "model.search_query_model", "") or "").strip()
        return configured or self.fast_model

    @property
    def gguf_path(self) -> str:
        return _resolve_path(str(self._env("GGUF_PATH", "model.gguf_path")))

    @property
    def quant_mode(self) -> str:
        return str(self._env("QUANT_MODE", "model.quant_mode"))

    @property
    def context_length(self) -> int:
        return int(self._env("CONTEXT_LENGTH", "model.context_length", 8192))

    # Generation
    @property
    def max_new_tokens(self) -> int:
        return int(self._env("MAX_NEW_TOKENS", "generation.max_new_tokens", 4096))

    @max_new_tokens.setter
    def max_new_tokens(self, value: int) -> None:
        self._raw.setdefault("generation", {})["max_new_tokens"] = int(value)

    @property
    def temperature(self) -> float:
        return float(self._env("TEMPERATURE", "generation.temperature", 0.65))

    @property
    def top_p(self) -> float:
        return float(self._env("TOP_P", "generation.top_p", 0.95))

    @property
    def repetition_penalty(self) -> float:
        return float(self._env("REPETITION_PENALTY", "generation.repetition_penalty", 1.05))

    @property
    def do_sample(self) -> bool:
        return _as_bool(self._env("DO_SAMPLE", "generation.do_sample", True))

    @property
    def default_think_mode(self) -> bool:
        return _as_bool(self._env("DEFAULT_THINK_MODE", "generation.default_think_mode", False))

    # Server
    @property
    def host(self) -> str:
        return str(self._env("APP_HOST", "server.host", "0.0.0.0"))

    @property
    def port(self) -> int:
        return int(self._env("APP_PORT", "server.port", 8000))

    @property
    def timezone(self) -> str:
        return str(self._env("APP_TIMEZONE", "server.timezone", "Asia/Shanghai"))

    @property
    def public_site_url(self) -> str:
        return str(self._env("PUBLIC_SITE_URL", "server.public_site_url", "http://localhost:8000"))

    @property
    def max_history_turns(self) -> int:
        return int(self._env("MAX_HISTORY_TURNS", "server.max_history_turns", 20))

    # Databases
    @property
    def mongo_uri(self) -> str:
        return str(self._env("MONGO_URI", "database.mongodb.uri", "mongodb://localhost:27017/"))

    @property
    def mongo_database(self) -> str:
        return str(self._env("MONGO_DATABASE", "database.mongodb.database", "pepper_chat_db"))

    @property
    def pgvector_connection_uri(self) -> str:
        return str(self._env("PGVECTOR_CONNECTION_URI", "database.pgvector.connection_uri"))

    @property
    def pgvector_collection(self) -> str:
        return str(self._env("PGVECTOR_COLLECTION", "database.pgvector.collection", "users_memory"))

    # Knowledge RAG
    @property
    def knowledge_rag_enabled(self) -> bool:
        return _as_bool(self._env("KNOWLEDGE_RAG_ENABLED", "knowledge_rag.enabled", True))

    @property
    def knowledge_rag_collection(self) -> str:
        return str(self._env("KNOWLEDGE_RAG_COLLECTION", "knowledge_rag.collection", "mof_finetune_knowledge"))

    @property
    def knowledge_rag_top_k(self) -> int:
        return int(self._env("KNOWLEDGE_RAG_TOP_K", "knowledge_rag.top_k", 5))

    @property
    def knowledge_rag_score_threshold(self) -> float:
        return float(self._env("KNOWLEDGE_RAG_SCORE_THRESHOLD", "knowledge_rag.score_threshold", 0.37))

    @property
    def knowledge_rag_max_context_chars(self) -> int:
        return int(self._env("KNOWLEDGE_RAG_MAX_CONTEXT_CHARS", "knowledge_rag.max_context_chars", 6500))

    # Services
    @property
    def ollama_base_url(self) -> str:
        return str(self._env("OLLAMA_HOST", "services.ollama.base_url", "http://localhost:11434"))

    @property
    def ollama_embedding_model(self) -> str:
        return str(self._env("OLLAMA_EMBEDDING_MODEL", "services.ollama.embedding_model", "nomic-embed-text"))

    @property
    def ollama_num_ctx_cap(self) -> int:
        return int(self._env("OLLAMA_NUM_CTX_CAP", "services.ollama.num_ctx_cap", 8192))

    # ── Ollama HTTP timeouts ─────────────────────────────────────────────────
    #
    # The ollama client ships with no timeouts at all. Without these, a wedged
    # Ollama blocks every caller forever and, because those calls occupy the
    # shared asyncio thread pool, takes the whole application down with it.
    #
    # `read` is per-read rather than total, so it only needs to cover the gap
    # before the first token (queue wait behind the parallel slots), not the
    # length of a long answer.

    @property
    def ollama_connect_timeout(self) -> float:
        """Seconds to establish the TCP connection. Ollama is local; be strict."""
        return float(self._env("OLLAMA_CONNECT_TIMEOUT", "services.ollama.connect_timeout", 10.0))

    @property
    def ollama_read_timeout(self) -> float:
        """Seconds to wait for the next chunk before giving up on a response."""
        return float(self._env("OLLAMA_READ_TIMEOUT", "services.ollama.read_timeout", 120.0))

    @property
    def ollama_write_timeout(self) -> float:
        return float(self._env("OLLAMA_WRITE_TIMEOUT", "services.ollama.write_timeout", 30.0))

    @property
    def ollama_pool_timeout(self) -> float:
        """Seconds to wait for a free connection from the client pool."""
        return float(self._env("OLLAMA_POOL_TIMEOUT", "services.ollama.pool_timeout", 15.0))

    @property
    def ollama_num_ctx(self) -> int:
        """The single context window used for every chat request.

        Must not vary per request: Ollama reloads the model whenever num_ctx
        changes (measured ~7.8s per change on this hardware), and a reload
        stalls all other in-flight requests. Size it for the largest workload
        (agent mode with a long PDF) and leave it alone.

        VRAM cost scales with num_ctx x OLLAMA_NUM_PARALLEL, so raise this only
        after checking headroom with `nvidia-smi`.
        """
        return int(self._env("OLLAMA_NUM_CTX", "services.ollama.num_ctx", self.ollama_num_ctx_cap))

    @property
    def ollama_num_gpu(self) -> int:
        return int(self._env("OLLAMA_NUM_GPU", "services.ollama.num_gpu", 99))

    @property
    def ollama_num_thread(self) -> int:
        return int(self._env("OLLAMA_NUM_THREAD", "services.ollama.num_thread", 4))

    @property
    def smtp_host(self) -> str:
        return str(self._env("SMTP_HOST", "services.smtp.host", "smtp.gmail.com"))

    @property
    def smtp_port(self) -> int:
        return int(self._env("SMTP_PORT", "services.smtp.port", 587))

    @property
    def smtp_username(self) -> str:
        return str(self._env("SMTP_USERNAME", "services.smtp.username", ""))

    @property
    def smtp_app_password(self) -> str:
        # Google shows app passwords grouped with spaces; SMTP expects the raw 16-character token.
        return "".join(str(self._env("SMTP_APP_PASSWORD", "services.smtp.app_password", "")).split())

    @property
    def smtp_from_name(self) -> str:
        return str(self._env("SMTP_FROM_NAME", "services.smtp.from_name", "bisnes.ai"))

    @property
    def google_oauth_client_id(self) -> str:
        return str(self._env("GOOGLE_OAUTH_CLIENT_ID", "services.google.oauth_client_id", ""))

    @property
    def google_login_oauth_client_id(self) -> str:
        return str(self._env("GOOGLE_LOGIN_OAUTH_CLIENT_ID", "services.google.login_oauth_client_id", self.google_oauth_client_id))

    @property
    def google_oauth_client_secret(self) -> str:
        return str(self._env("GOOGLE_OAUTH_CLIENT_SECRET", "services.google.oauth_client_secret", ""))

    @property
    def google_client_secret_file(self) -> str:
        return _resolve_path(str(self._env("GOOGLE_CLIENT_SECRET_FILE", "services.google.client_secret_file", "")))

    # Security
    @property
    def jwt_secret(self) -> str:
        secret = str(self._env("JWT_SECRET", "security.jwt_secret", "")).strip()
        if len(secret) < 32:
            raise RuntimeError(
                "JWT_SECRET must be set to a random value of at least 32 characters in an ignored secrets file."
            )
        return secret

    @property
    def google_token_encryption_key(self) -> str:
        return str(
            self._env("GOOGLE_TOKEN_ENCRYPTION_KEY", "security.google_token_encryption_key", "")
        ).strip()

    @property
    def jwt_algorithm(self) -> str:
        return str(self._env("JWT_ALGORITHM", "security.jwt_algorithm", "HS256"))

    @property
    def jwt_expiration_hours(self) -> int:
        return int(self._env("JWT_EXPIRATION_HOURS", "security.jwt_expiration_hours", 24))

    # Search
    @property
    def bing_api_key(self) -> str:
        return ""

    @property
    def max_queries(self) -> int:
        return int(self._env("SEARCH_MAX_QUERIES", "search.max_queries", 5))

    @property
    def max_results_per_query(self) -> int:
        return int(self._env("SEARCH_MAX_RESULTS_PER_QUERY", "search.max_results_per_query", 12))

    @property
    def max_results_total(self) -> int:
        return int(self._env("SEARCH_MAX_RESULTS_TOTAL", "search.max_results_total", 10))

    @property
    def min_results_total(self) -> int:
        return int(self._env("SEARCH_MIN_RESULTS_TOTAL", "search.min_results_total", 5))

    @property
    def snippet_length(self) -> int:
        return int(self._env("SEARCH_SNIPPET_LENGTH", "search.snippet_length", 250))

    @property
    def search_timeout_seconds(self) -> float:
        return float(self._env("SEARCH_TIMEOUT_SECONDS", "search.timeout_seconds", 2))

    @property
    def search_html_timeout_seconds(self) -> float:
        return float(self._env("SEARCH_HTML_TIMEOUT_SECONDS", "search.html_timeout_seconds", 1.5))

    @property
    def search_deadline_seconds(self) -> float:
        return float(self._env("SEARCH_DEADLINE_SECONDS", "search.deadline_seconds", 5))

    @property
    def search_max_workers(self) -> int:
        return int(self._env("SEARCH_MAX_WORKERS", "search.max_workers", 5))

    @property
    def blacklist_domains(self) -> list:
        return list(self.get("search.blacklist_domains", []))

    # Web Search (new Brave/Tavily/Crawl4AI system)
    @property
    def brave_search_api_key(self) -> str:
        return str(self._env("BRAVE_SEARCH_API_KEY", "web_search.brave_api_key", ""))

    @property
    def tavily_api_key(self) -> str:
        return str(self._env("TAVILY_API_KEY", "web_search.tavily_api_key", ""))

    @property
    def crawl4ai_base_url(self) -> str:
        return str(self._env("CRAWL4AI_BASE_URL", "web_search.crawl4ai_base_url", ""))

    @property
    def web_cache_ttl_hours(self) -> int:
        return int(self._env("WEB_CACHE_TTL_HOURS", "web_search.cache_ttl_hours", 24))

    @property
    def web_search_max_results(self) -> int:
        return int(self._env("WEB_SEARCH_MAX_RESULTS", "web_search.max_results", 10))

    @property
    def web_search_max_pages(self) -> int:
        return int(self._env("WEB_SEARCH_MAX_PAGES", "web_search.max_pages", 4))

    # Interactive chat uses a tighter budget than the standalone research API:
    # the user is watching a spinner, so fewer pages and a hard wall-clock cap.
    @property
    def web_search_chat_max_results(self) -> int:
        return int(self._env("WEB_SEARCH_CHAT_MAX_RESULTS", "web_search.chat_max_results", 6))

    @property
    def web_search_chat_max_pages(self) -> int:
        return int(self._env("WEB_SEARCH_CHAT_MAX_PAGES", "web_search.chat_max_pages", 2))

    @property
    def web_search_chat_deadline_seconds(self) -> float:
        """Hard wall-clock cap for web research inside a chat turn.

        On expiry the turn degrades to answering without live sources rather
        than leaving the user on an open-ended heartbeat.
        """
        return float(self._env("WEB_SEARCH_CHAT_DEADLINE_SECONDS", "web_search.chat_deadline_seconds", 15))

    def print_summary(self) -> None:
        secret_state = "SET" if self.smtp_app_password else "not set"
        google_secret = "SET" if self.google_oauth_client_secret or Path(self.google_client_secret_file).exists() else "not set"
        print(f"""
  ┌─ Config Summary ──────────────────────────────────┐
  │  Profile       : {self.profile}
  │  Loaded files   : {", ".join(self.loaded_paths) if self.loaded_paths else "built-ins only"}
  │  Server        : {self.host}:{self.port}
  │  Public URL    : {self.public_site_url}
  │  MongoDB       : {self.mongo_uri} / {self.mongo_database}
  │  PGVector      : {self.pgvector_connection_uri}
  │  Ollama        : {self.ollama_base_url}
  │  Think model   : {self.think_model}
  │  Fast model    : {self.fast_model}
  │  Search planner: {self.search_query_model}
  │  Context length: {self.context_length} tokens
  │  SMTP password : {secret_state}
  │  Google secret : {google_secret}
  └───────────────────────────────────────────────────┘""")


cfg = _Config()
