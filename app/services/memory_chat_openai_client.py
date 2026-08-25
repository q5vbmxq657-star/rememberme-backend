from __future__ import annotations

import os

from openai import OpenAI


def make_memory_chat_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    timeout_seconds = _bounded_timeout(
        os.getenv("OPENAI_MEMORY_CHAT_TIMEOUT_SECONDS", "25")
    )
    return OpenAI(
        api_key=api_key,
        timeout=timeout_seconds,
        max_retries=0,
    )


def _bounded_timeout(raw_value: str) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        value = 25.0
    return min(max(value, 5.0), 60.0)
