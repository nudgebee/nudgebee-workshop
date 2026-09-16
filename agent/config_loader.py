#!/usr/bin/env python3
"""
===============================================================================
SRE Agent Configuration & Prompt Loader (config_loader.py)
===============================================================================
Zero-dependency YAML parser and prompt assembler for SRE Incident Investigation.
Loads config.yaml and prompts.yaml and synthesizes active agent settings.
===============================================================================
"""

import os
from typing import Dict, List, Any, Optional

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(AGENT_DIR, "config.yaml")
PROMPTS_FILE = os.path.join(AGENT_DIR, "prompts.yaml")


def load_yaml_file(file_path: str) -> Dict[str, Any]:
    """Loads YAML file using PyYAML if available, or robust zero-dependency parser if not."""
    if not os.path.exists(file_path):
        return {}
    try:
        import yaml
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass

    root: Dict[str, Any] = {}
    current_key: Optional[str] = None
    current_sub_key: Optional[str] = None
    in_block = False
    block_indent = 0
    block_lines: List[str] = []

    def flush_block():
        nonlocal in_block, block_lines, current_key, current_sub_key
        if not in_block:
            return
        block_text = "".join(block_lines).rstrip()
        if current_sub_key and current_key in root and isinstance(root[current_key], dict):
            root[current_key][current_sub_key] = block_text
        elif current_key:
            root[current_key] = block_text
        in_block = False
        block_lines = []

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()

            # Handle multiline block scalar (| or >)
            if in_block:
                if not stripped:
                    block_lines.append("\n")
                    continue
                indent = len(line) - len(line.lstrip(" "))
                if indent >= block_indent:
                    content = line[block_indent:] if len(line) >= block_indent else line.lstrip(" ")
                    block_lines.append(content)
                    continue
                else:
                    flush_block()

            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(line.lstrip(" "))

            # Check list item
            if stripped.startswith("- "):
                item = stripped[2:].split("#")[0].strip().strip("\"'")
                if current_key:
                    if current_key not in root or not isinstance(root[current_key], list):
                        root[current_key] = []
                    root[current_key].append(item)
                continue

            if ":" in stripped:
                parts = stripped.split(":", 1)
                k = parts[0].strip()
                v = parts[1].split("#")[0].strip()

                if indent == 0:
                    flush_block()
                    current_key = k
                    current_sub_key = None
                    if v in ("|", ">"):
                        in_block = True
                        block_indent = 2
                        block_lines = []
                    elif not v:
                        pass
                    elif v.lower() == "true":
                        root[k] = True
                    elif v.lower() == "false":
                        root[k] = False
                    elif v.isdigit():
                        root[k] = int(v)
                    else:
                        root[k] = v.strip("\"'")
                else:
                    # Sub-key under current_key
                    if current_key:
                        if current_key not in root or not isinstance(root[current_key], dict):
                            root[current_key] = {}
                        current_sub_key = k
                        if v in ("|", ">"):
                            in_block = True
                            block_indent = indent + 2
                            block_lines = []
                        elif not v:
                            pass
                        elif v.lower() == "true":
                            root[current_key][k] = True
                        elif v.lower() == "false":
                            root[current_key][k] = False
                        elif v.isdigit():
                            root[current_key][k] = int(v)
                        else:
                            root[current_key][k] = v.strip("\"'")

    flush_block()
    return root


def load_config() -> Dict[str, Any]:
    """Loads configuration and prompts from YAML files and environment variables."""
    cfg_data = load_yaml_file(CONFIG_FILE)
    prompt_data = load_yaml_file(PROMPTS_FILE)

    config = {
        # 1. MODEL SELECTION
        "model": os.getenv("MODEL_NAME", cfg_data.get("model", "gemini-3.8-flash")),
        "api_base": os.getenv("OPENAI_BASE_URL", cfg_data.get("api_base", "https://llm-gateway.dev.nudgebee.pollux.in/v1")),
        "api_key": os.getenv("OPENAI_API_KEY", cfg_data.get("api_key", "")),

        # 2. ACTIVE TOOLS
        "enabled_tools": cfg_data.get("enabled_tools", [
            "query_prometheus",
            "get_k8s_events",
            "query_pod_logs",
            "inspect_topology",
            "ask_human_approval",
            "get_deploy_history",
            "search_incident_history",
        ]),

        # 3. TARGET ENVIRONMENT & SCENARIO
        "namespace": cfg_data.get("namespace", "group-1"),
        "scenario": cfg_data.get("scenario", "badDeploy1405"),

        # 4. CONTEXT MODE & PROMPT CACHING
        "context_mode": cfg_data.get("context_mode", "filtered_regex"),
        "enable_prompt_caching": cfg_data.get("enable_prompt_caching", True),

        # 5. EPISODIC MEMORY
        "enable_memory_recall": cfg_data.get("enable_memory_recall", cfg_data.get("enable_memory", False)),
        "persist_verified_resolution": cfg_data.get("persist_verified_resolution", True),
        "enable_memory": cfg_data.get("enable_memory", False),

        # 6. PERSONA & SYSTEM PROMPT
        "system_prompt": prompt_data.get("system_prompt", (
            "You are an Autonomous SRE Incident Investigation Agent. "
            "Diagnose production microservice incidents running on Kubernetes."
        )),

        # 7. REASONING MODE & GUARDRAILS
        "reasoning_mode": prompt_data.get("reasoning_mode", "deep_rca"),
        "security_guardrails": prompt_data.get("security_guardrails", ""),
        "reasoning_templates": prompt_data.get("reasoning_templates", {}),
        "scenario_guidance": prompt_data.get("scenario_guidance", {}),

        # 8. INITIAL USER PROMPT
        "initial_user_prompt": prompt_data.get("initial_user_prompt", (
            "Investigate active incident in namespace '{namespace}'. "
            "Storefront frontend is experiencing anomalies under scenario '{scenario}'."
        )),

        # 9. INVESTIGATION LIMITS
        "max_turns": cfg_data.get("max_turns", 10),

        # 10. LOGGING DIRECTORY
        "log_dir": os.path.join(AGENT_DIR, "logs"),
    }
    return config


# Blended token pricing per 1,000 tokens
MODEL_RATES = {
    "mock": 0.0000,
    "NVIDIA-Nemotron-3-Nano-30B-A3B-BF16": 0.0001,
    "qwen/qwen3-235b-a22b-instruct-2507-maas": 0.0008,
    "gemini-3.8-flash": 0.0003,
    "nb-fast": 0.0002,
    "nb-cheap": 0.0001,
    "nb-smart": 0.0030,
    "gemini-1.5-flash": 0.0002,
    "gemini-1.5-pro": 0.0040,
    "gpt-4o-mini": 0.0003,
    "gpt-4o": 0.0060,
    "claude-3-5-sonnet": 0.0050,
}


def assemble_system_prompt(config: Dict[str, Any], scenario: str) -> str:
    """Combines base persona, security guardrails, reasoning mode, and scenario guidance."""
    raw_base = config.get("system_prompt", "")
    base = "\n".join(raw_base) if isinstance(raw_base, list) else str(raw_base).strip()

    raw_guardrails = config.get("security_guardrails", "")
    guardrails = "\n".join(raw_guardrails) if isinstance(raw_guardrails, list) else str(raw_guardrails).strip()

    mode = str(config.get("reasoning_mode", "deep_rca"))
    templates = config.get("reasoning_templates", {})
    reasoning = ""
    if isinstance(templates, dict):
        raw_reasoning = templates.get(mode, "")
        reasoning = "\n".join(raw_reasoning) if isinstance(raw_reasoning, list) else str(raw_reasoning).strip()

    guidance_map = config.get("scenario_guidance", {})
    guidance = ""
    if isinstance(guidance_map, dict):
        raw_guidance = guidance_map.get(scenario, "")
        guidance = "\n".join(raw_guidance) if isinstance(raw_guidance, list) else str(raw_guidance).strip()

    parts = []
    if base:
        parts.append(base)
    if guardrails:
        parts.append(f"=== SECURITY & GUARDRAIL POLICY ===\n{guardrails}")
    if reasoning:
        parts.append(f"=== REASONING & OUTPUT SPECIFICATION ({mode.upper()}) ===\n{reasoning}")
    if guidance:
        parts.append(f"=== SCENARIO DOMAIN KNOWLEDGE ===\n{guidance}")

    # Episodic memory injection when active
    is_recall_active = config.get("enable_memory_recall", config.get("enable_memory", False))
    if is_recall_active:
        parts.append(
            "=== EPISODIC MEMORY DIRECTIVE (MEMORY ACTIVE) ===\n"
            "Episodic memory recall is ENABLED. You have access to persistent past incident post-mortems "
            "via 'search_incident_history'. You MUST query past incidents to determine if this symptom has occurred "
            "in previous runs, cite the prior incident ID and date, compare symptoms, and state whether the previous fix held or recurred."
        )

    return "\n\n".join(parts)
