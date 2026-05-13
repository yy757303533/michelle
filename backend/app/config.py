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
    log_file: str = "./logs/michelle.log"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://localhost:5173"
    admin_token: str = ""
    """Optional bearer-style token for mutating/admin API access.
    Empty keeps local-dev zero-auth behavior. When set, clients must send
    X-Michelle-Admin-Token for unsafe /api requests and all /api/settings reads."""
    default_admin_username: str = "admin"
    default_admin_password: str = ""
    """Optional bootstrap admin password.
    Leave empty to generate a one-time local password on first startup."""

    # ── Data ──
    database_url: str = "postgresql+asyncpg://michelle:michelle@127.0.0.1:5432/michelle"
    artifacts_dir: str = "./artifacts"

    # ── Run orchestration ──
    max_concurrent_runs: int = 2
    """How many cases may execute simultaneously. Each = 1 Chromium + 1 claude CLI."""
    executor_loop: str = "auto"
    """Execution loop strategy: auto | generic_openai | claude_cli."""
    generic_agent_max_turns: int = 30
    """Safety cap for Michelle's own generic JSON-action agent loop."""
    playwright_mcp_package: str = "@playwright/mcp@0.0.40"
    """npx package spec for the Playwright MCP server. Pin to a specific
    version (e.g. `@playwright/mcp@0.0.20`) in production via .env to avoid
    silent behaviour drift from the upstream `@latest` tag."""
    playwright_mcp_cache_dir: str = ""
    """Shared npm cache for `npx @playwright/mcp`. Empty defaults to
    `<artifacts_dir>/.npm-cache`. Do not make this per-run; cold npx installs
    are slow and can make MCP initialization time out."""
    playwright_mcp_npm_registry: str = ""
    """Optional npm registry override for Playwright MCP downloads, e.g.
    `https://registry.npmmirror.com` in restricted networks."""
    playwright_mcp_startup_timeout_seconds: int = 180
    """How long to wait for MCP initialize/tools-list during startup."""

    # ── LLM: Claude CLI (primary, subscription) ──
    claude_cli_path: str = "claude"
    claude_timeout_seconds: int = 180
    # When set, these are injected into the env of every `claude` subprocess
    # spawned by claude_runner / claude_cli, so the CLI talks to an
    # Anthropic-compatible gateway instead of the user's
    # subscription. Leave empty to use the user's normal subscription login.
    anthropic_base_url: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    claude_code_attribution_header: str = ""

    # ── LLM: Codex CLI (secondary, ChatGPT subscription) ──
    codex_cli_path: str = "codex"
    codex_enabled: bool = False  # off by default; flip to True in .env if installed
    codex_timeout_seconds: int = 180
    codex_model: str = "gpt-5.5"
    codex_reasoning_effort: str = "low"

    # ── Observability ──
    logfire_token: str = ""
    logfire_project: str = "michelle"
    otel_service_name: str = "michelle-backend"

    # ── Default test target (smoke-test fallback) ──
    default_target_url: str = ""
    default_target_username: str = ""
    default_target_password: str = ""

    # ── Temporary inbox for real registration E2E flows ──
    temp_email_provider: str = "mail_tm"
    """Temporary inbox provider for registration flows: mail_tm | none."""
    temp_email_base_url: str = "https://api.mail.tm"
    temp_email_code_timeout_seconds: int = 120
    temp_email_poll_interval_seconds: int = 5

    # ── External developer context ──
    michelle_workspace_root: str = ""
    """Optional external zstack-workspace root used for PRD/code/dev context."""
    michelle_zdev_mcp_command: str = "node"
    michelle_zdev_mcp_args: str = ""
    """Shell-like argument string, e.g. /path/to/zstack-dev-mcp/dist/index.js."""
    michelle_zdev_mcp_cwd: str = ""
    michelle_zdev_mcp_timeout_seconds: int = 60

    @property
    def artifacts_path(self) -> Path:
        p = Path(self.artifacts_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def playwright_mcp_cache_path(self) -> Path:
        p = (
            Path(self.playwright_mcp_cache_dir).resolve()
            if self.playwright_mcp_cache_dir
            else (self.artifacts_path / ".npm-cache")
        )
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def has_logfire(self) -> bool:
        return bool(self.logfire_token)


settings = Settings()
