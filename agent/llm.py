"""
llm.py — LiteLLM helper utilities.

Centralizes LiteLLM usage and response parsing so other modules
can focus on business logic and error handling.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from litellm import completion

logger = logging.getLogger(__name__)


def chat_completion(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    response_format: Optional[dict[str, str]] = None,
) -> Any:
    """Run a chat completion via LiteLLM and return the raw response."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format
    return completion(**kwargs)


def get_message_content(response: Any) -> str:
    """Extract the first message content from a LiteLLM response."""
    try:
        return response["choices"][0]["message"]["content"]
    except Exception as exc:
        raise ValueError("Unable to read completion content") from exc


def parse_json_object(raw: str) -> Optional[dict[str, Any]]:
    """Parse the first JSON object in a string, if any."""
    raw = raw.strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Fallback: extract content between the first and last brace
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    snippet = raw[start : end + 1]
    try:
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        logger.warning("Failed to parse JSON snippet from model output")

    return None
