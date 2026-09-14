#!/usr/bin/env python3
"""
===============================================================================
SRE Agent Episodic Memory Store (memory.py)
===============================================================================
Manages persistent post-mortem incident memory across agent runs.
Updates both machine-readable JSON (memory/episodic_memory.json) and
human-readable markdown ledger (memory/episodic_memory.md).
===============================================================================
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = os.path.join(AGENT_DIR, "memory")
JSON_PATH = os.path.join(MEMORY_DIR, "episodic_memory.json")
MD_PATH = os.path.join(MEMORY_DIR, "episodic_memory.md")


def get_all_incidents() -> List[Dict[str, Any]]:
    """Loads all past incidents from the JSON store."""
    if not os.path.exists(JSON_PATH):
        return []
    try:
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_to_episodic_memory(diagnosis: str, scenario: str, namespace: str, model: str):
    """Saves the completed incident diagnosis to memory/episodic_memory.json and episodic_memory.md."""
    if not diagnosis or len(diagnosis.strip()) < 30:
        return

    os.makedirs(MEMORY_DIR, exist_ok=True)

    now = datetime.now()
    inc_id = f"INC-{now.strftime('%Y%m%d-%H%M%S')}"
    timestamp_str = now.strftime("%d %B, %H:%M UTC")

    title = f"Incident investigation on {scenario} ({namespace})"
    for line in diagnosis.splitlines():
        if "ROOT CAUSE" in line:
            title = line.replace("ROOT CAUSE IDENTIFIED:", "").strip()
            break

    remediation = "Rollback or configuration adjustment documented in audit trace."
    for line in diagnosis.splitlines():
        if "Remediation" in line or "kubectl" in line:
            remediation = line.strip()
            break

    service = "microservices"
    for candidate in ["checkout", "product-catalog", "frontend", "email", "payment", "cart", "shipping"]:
        if candidate in diagnosis.lower():
            service = candidate
            break

    record = {
        "incident_id": inc_id,
        "date": timestamp_str,
        "timestamp": now.isoformat(),
        "namespace": namespace,
        "scenario": scenario,
        "service": service,
        "model": model,
        "title": title,
        "keywords": [scenario.lower(), service.lower(), "failure", "timeout", "latency", "starvation", "leak"],
        "symptoms": f"Anomalies detected in namespace {namespace} under scenario {scenario}.",
        "root_cause": diagnosis[:400].replace("\n", " "),
        "fix": remediation,
        "did_fix_hold": f"Recorded from run by {model} at {timestamp_str}."
    }

    # 1. Update JSON store
    data = get_all_incidents()
    data.append(record)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # 2. Append to Markdown ledger
    md_entry = (
        f"\n### [{inc_id}] · {timestamp_str} · {service} ({namespace})\n"
        f"- **Scenario**: `{scenario}` | **Model**: `{model}`\n"
        f"- **Title**: {title}\n"
        f"- **Root Cause**: {record['root_cause'][:250]}...\n"
        f"- **Remediation**: `{remediation}`\n"
        f"- **Ledger Status**: Saved to episodic store (Recallable via `enable_memory: true`)\n"
        f"---\n"
    )
    with open(MD_PATH, "a", encoding="utf-8") as f:
        f.write(md_entry)

    print(f"  💾 Episodic Memory Updated: {inc_id} saved to memory/episodic_memory.md\n")
