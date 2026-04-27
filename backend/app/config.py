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

    # ── LLM: Claude (primary) ──
    claude_cli_path: str = "claude"
    claude_timeout_seconds: int = 180

    # ── LLM: MiniMax (fallback) ──
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    minimax_model_text: str = "MiniMax-Text-01"
    minimax_model_reasoning: str = "MiniMax-M2.7"

    # ── LLM: Flywheel (premium upgrade) ──
    flywheel_token: str = ""
    flywheel_base_url: str = "https://flywheel.zstack.io/v1/chat/completions"
    flywheel_model_premium: str = "anthropic/claude-opus-4.7"

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
    def has_logfire(self) -> bool:
        return bool(self.logfire_token)


settings = Settings()
