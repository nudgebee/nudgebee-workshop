#!/usr/bin/env python3
"""
===============================================================================
OpenAI-Compatible LLM Gateway Client (llm_client.py)
===============================================================================
Zero-dependency HTTP client using urllib.request to interact with any
OpenAI-compatible /v1/chat/completions endpoint with native function calling.
===============================================================================
"""

import json
import time
import urllib.error
import urllib.request
import urllib.parse
from typing import Dict, List, Any, Optional

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRY_BACKOFF_S = (3, 8)


def call_openai_chat_completions(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = None,
    timeout: int = 60
) -> Dict[str, Any]:
    """
    Invokes any OpenAI-compatible /v1/chat/completions endpoint with native function calling.
    Zero external dependencies required.
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
    elif tool_choice:
        # Gemini needs tool_choice="none" here or it calls a tool from the transcript's
        # history anyway; OpenAI rejects tool_choice without tools - see the retry below.
        payload["tool_choice"] = tool_choice

    # A room of attendees shares one gateway, so 429s and 5xx blips are routine; retry
    # them briefly rather than aborting a whole investigation on one bad response.
    backoffs = list(RETRY_BACKOFF_S) + [None]
    while True:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        backoff = backoffs[0]
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            # The body says *why* (unknown model, bad message order...); the status alone does not.
            detail = err.read().decode("utf-8", "replace")[:300].strip()
            if err.code == 400 and not tools and "tool_choice" in payload and "tool_choice" in detail:
                del payload["tool_choice"]
                continue
            if backoff is None or err.code not in RETRYABLE_STATUS:
                raise RuntimeError(f"HTTP Error {err.code}: {err.reason}" + (f" - {detail}" if detail else "")) from err
            print(f"  ⚠️ Gateway returned HTTP {err.code}; retrying in {backoff}s...")
        except urllib.error.URLError as err:
            if backoff is None:
                raise
            print(f"  ⚠️ Gateway unreachable ({err.reason}); retrying in {backoff}s...")
        backoffs.pop(0)
        time.sleep(backoff)
