"""Application settings — loaded from .env via pydantic-settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──
    app_name: str = "michelle"
    app_env: str = "dev"
    log_level: str = "INFO"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://localhost:5173"

    # ── Data ──
    database_url: str = "sqlite+aiosqlite:///./data/michelle.db"
    artifacts_dir: str = "./artifacts"

    # ── Run orchestration ──
    max_concurrent_runs: int = 2
    """How many cases may execute simultaneously. Each = 1 Chromium + 1 claude CLI."""

    # ── LLM: Claude CLI (primary, subscription) ──
    claude_cli_path: str = "claude"
    claude_timeout_seconds: int = 180

    # ── LLM: Codex CLI (secondary, ChatGPT subscription) ──
    codex_cli_path: str = "codex"
    codex_enabled: bool = False  # off by default; flip to True in .env if installed

    # ── LLM: MiniMax (HTTP, native protocol) ──
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    minimax_model_text: str = "MiniMax-Text-01"
    minimax_model_reasoning: str = "MiniMax-M2.7"

    # ── LLM: Flywheel proxy (premium upgrade) ──
    flywheel_token: str = ""
    flywheel_base_url: str = "https://flywheel.zstack.io/v1/chat/completions"
    flywheel_model_premium: str = "claude-opus-4-7"
    """Default model for the Flywheel proxy.

    Two naming conventions exist on Flywheel:
      - `anthropic/claude-opus-4.7` — OpenAI-compat response, but the `default`
        token group has no quota for the namespaced route (HTTP 402 quote_exceeded)
      - `claude-opus-4-7` — bare name, routes to a different distributor that
        DOES have capacity. Returns native Anthropic shape, which our client
        parses transparently.

    Opus 4.7 wins informal vision A/B against GPT-5.5 (less hallucination,
    catches more small UI details) so it's the default for diagnosis-with-
    screenshot routing. When quota runs out, the gateway falls through to
    MiniMax. Override via FLYWHEEL_MODEL_PREMIUM."""

    # ── LLM: Kimi / Moonshot (OpenAI-compatible) ──
    kimi_api_key: str = ""
    kimi_base_url: str = "https://api.moonshot.cn/v1/chat/completions"
    kimi_model: str = "kimi-k2-0905-preview"

    # ── LLM: Qwen / Alibaba DashScope (OpenAI-compatible mode) ──
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    qwen_model: str = "qwen3-max"

    # ── LLM: DeepSeek (OpenAI-compatible) ──
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1/chat/completions"
    deepseek_model: str = "deepseek-chat"

    # ── LLM: GLM / 智谱 (OpenAI-compatible) ──
    glm_api_key: str = ""
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    glm_model: str = "glm-4.7"

    # ── LLM: Gemini (OpenAI-compatible endpoint) ──
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    gemini_model: str = "gemini-2.5-pro"

    # ── LLM: arbitrary 中转站 (free-form OpenAI-compatible relay) ──
    relay_api_key: str = ""
    relay_base_url: str = ""
    """Use for any OpenAI-compatible relay (OneAPI/NewAPI/OpenRouter/...)."""
    relay_model: str = ""
    relay_name: str = "relay"
    """Logical name shown in dashboard / probe responses."""

    # ── Observability ──
    logfire_token: str = ""
    logfire_project: str = "michelle"
    otel_service_name: str = "michelle-backend"

    # ── Default test target (Day 2 verification) ──
    default_target_url: str = "http://172.25.17.105:5000/"
    default_target_username: str = "admin"
    default_target_password: str = "password"

    @property
    def artifacts_path(self) -> Path:
        p = Path(self.artifacts_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def has_minimax(self) -> bool:
        return bool(self.minimax_api_key)

    @property
    def has_flywheel(self) -> bool:
        return bool(self.flywheel_token)

    @property
    def has_kimi(self) -> bool:
        return bool(self.kimi_api_key)

    @property
    def has_qwen(self) -> bool:
        return bool(self.qwen_api_key)

    @property
    def has_deepseek(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def has_glm(self) -> bool:
        return bool(self.glm_api_key)

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_relay(self) -> bool:
        return bool(self.relay_api_key and self.relay_base_url and self.relay_model)

    @property
    def has_logfire(self) -> bool:
        return bool(self.logfire_token)


settings = Settings()
