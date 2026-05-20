"""Shared Claude (Anthropic) API client.

A deliberately thin wrapper around the Anthropic Messages API so the rest of
the codebase has exactly one place that knows how to talk to Claude. Item 6
(natural-language model tuning) is the first caller; Jonah's natural-language
interface (Phase 2) will reuse this same client heavily.

Design notes:
  - The `anthropic` package is imported lazily inside the call so the app still
    boots (and unrelated features keep working) even if the dependency or the
    API key is missing.
  - Configuration (API key + model) comes from app.core.config.settings, which
    reads ANTHROPIC_API_KEY / ANTHROPIC_MODEL from the environment / .env.
  - LLMError is raised for every failure mode (no key, package missing, API
    error) so callers can catch one exception type and surface a clean message.
"""
from __future__ import annotations

from app.core.config import settings


class LLMError(RuntimeError):
    """Raised when a Claude API call cannot be completed."""


def is_configured() -> bool:
    """True when an Anthropic API key is present."""
    return settings.has_anthropic_key


def call_claude(
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    model: str | None = None,
) -> str:
    """Send a single-turn message to Claude and return the text response.

    Args:
        prompt: The user message.
        system: Optional system prompt.
        max_tokens: Cap on the response length.
        temperature: Sampling temperature (low by default for structured output).
        model: Override the configured model string.

    Returns:
        The concatenated text of Claude's response.

    Raises:
        LLMError: if no API key is configured, the SDK is missing, or the API
            call fails.
    """
    if not settings.has_anthropic_key:
        raise LLMError(
            "No Anthropic API key configured. Set ANTHROPIC_API_KEY in your .env "
            "(and in the Railway environment for the deployed app)."
        )

    try:
        import anthropic
    except ImportError as e:  # pragma: no cover - depends on install state
        raise LLMError(
            "The 'anthropic' package is not installed. Run "
            "`pip install -r requirements.txt`."
        ) from e

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    kwargs: dict = {
        "model": model or settings.anthropic_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    try:
        resp = client.messages.create(**kwargs)
    except Exception as e:  # anthropic.APIError and friends
        raise LLMError(f"Claude API call failed: {e}") from e

    # Concatenate all text blocks in the response.
    parts = [block.text for block in resp.content if getattr(block, "type", None) == "text"]
    text = "".join(parts).strip()
    if not text:
        raise LLMError("Claude returned an empty response.")
    return text
