"""LLM Gateway — provider-agnostic chat with auto-fallback."""

from app.llm.base import (
    BaseChatClient,
    FallbackableLLMError,
    LLMAuthError,
    LLMError,
    LLMResponseFormatError,
    LLMResult,
    LLMTimeoutError,
    QuotaExceededError,
    RateLimitError,
)
from app.llm.claude_cli import ClaudeCLIClient
from app.llm.flywheel import FlywheelClient
from app.llm.gateway import (
    GatewayClient,
    LLMGateway,
    build_default_clients,
    get_gateway,
    reset_gateway,
)
from app.llm.minimax import MiniMaxClient
from app.llm.prompts.registry import PromptNotFoundError, load_prompt, prompt_id, render

__all__ = [
    # base
    "BaseChatClient",
    "LLMResult",
    "LLMError",
    "FallbackableLLMError",
    "RateLimitError",
    "QuotaExceededError",
    "LLMTimeoutError",
    "LLMAuthError",
    "LLMResponseFormatError",
    # clients
    "ClaudeCLIClient",
    "MiniMaxClient",
    "FlywheelClient",
    # gateway
    "GatewayClient",
    "LLMGateway",
    "build_default_clients",
    "get_gateway",
    "reset_gateway",
    # prompts
    "load_prompt",
    "render",
    "prompt_id",
    "PromptNotFoundError",
]
