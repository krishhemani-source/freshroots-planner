"""AI step: suggestions for Track B exceptions. AI_MODE=mock (default) or live."""

from planner.ai.base import AIClient
from planner.config import Config, env


def get_ai_client(cfg: Config) -> AIClient:
    if env("AI_MODE", "mock").lower() == "live":
        from planner.ai.gemini_client import GeminiClient
        return GeminiClient(model=env("GEMINI_MODEL") or cfg.ai["model"], timeout_seconds=cfg.ai["request_timeout_seconds"])
    from planner.ai.mock_client import MockAIClient
    return MockAIClient()
