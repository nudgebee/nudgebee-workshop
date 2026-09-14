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
import urllib.request
import urllib.parse
from typing import Dict, List, Any, Optional


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
        payload["tool_choice"] = tool_choice

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
