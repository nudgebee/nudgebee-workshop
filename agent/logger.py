#!/usr/bin/env python3
"""
===============================================================================
SRE Agent Audit Logging Facility (logger.py)
===============================================================================
Records human-readable transcripts (.log) and complete machine-parseable
JSON traces (.json) with trajectory data, tool metrics, and scorecards.
===============================================================================
"""

import os
import json
import shutil
from datetime import datetime
from typing import Dict, List, Any, Optional


class AuditLogger:
    """Records human-readable transcripts and complete machine-parseable JSON traces."""
    def __init__(
        self,
        log_dir: str,
        namespace: str,
        system_prompt: str,
        tool_schemas: List[Dict[str, Any]],
        max_turns: int = 10,
        config_snapshot: Optional[Dict[str, Any]] = None
    ):
        self.log_dir = log_dir
        self.max_turns = max_turns
        os.makedirs(log_dir, exist_ok=True)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_filename = os.path.join(log_dir, f"investigation_{self.timestamp}_{namespace}.log")
        self.json_filename = os.path.join(log_dir, f"investigation_{self.timestamp}_{namespace}.json")
        self.latest_log = os.path.join(log_dir, "investigation_latest.log")
        self.latest_json = os.path.join(log_dir, "investigation_latest.json")

        self.trace_data = {
            "metadata": {
                "timestamp": self.timestamp,
                "created_at": datetime.now().isoformat(),
                "config": config_snapshot or {},
            },
            "prompt_archive": {
                "system_prompt": system_prompt,
                "tool_schemas": tool_schemas,
            },
            "trajectory": [],
            "final_diagnosis": None,
            "scorecard": None,
        }

        # Initialize readable log header
        header = [
            f"======================================================================",
            f"🚨 SRE AGENT AUDIT LOG · {self.timestamp} · NAMESPACE: {namespace}",
            f"======================================================================",
            f"📌 SYSTEM PROMPT:\n{system_prompt.strip()}\n",
            f"🛠️ ACTIVE TOOL SCHEMAS ({len(tool_schemas)} tools registered):",
            json.dumps(tool_schemas, indent=2),
            f"======================================================================\n",
        ]
        self._write_log("\n".join(header) + "\n")

    def _write_log(self, text: str):
        with open(self.log_filename, "a", encoding="utf-8") as f:
            f.write(text)

    def log_turn(
        self,
        turn: int,
        messages_snapshot: List[Dict[str, Any]],
        thought: str,
        tool_call: Optional[Dict[str, Any]],
        observation: Optional[str],
        metrics: Dict[str, Any]
    ):
        entry = {
            "turn": turn,
            "messages_input": list(messages_snapshot),
            "thought": thought,
            "tool_call": tool_call,
            "observation": observation,
            "metrics": metrics,
        }
        self.trace_data["trajectory"].append(entry)

        log_chunk = [
            f"----------------------------------------------------------------------",
            f"▶ [Turn {turn}/{self.max_turns}]",
            f"  💭 Reasoning / Thought: {thought}",
        ]
        if tool_call:
            log_chunk.append(f"  ⚡ Tool Call: {tool_call['name']}({json.dumps(tool_call.get('args', {}))})")
        if observation is not None:
            log_chunk.append(f"  📥 Raw Tool Observation ({len(observation)} chars):")
            for line in observation.splitlines():
                log_chunk.append(f"     {line}")
        log_chunk.append(
            f"  ⏱️  Duration: {metrics.get('duration_s', 0):.2f}s | "
            f"Tokens: {metrics.get('active_prompt_tokens', 0)} prompt "
            f"(+{metrics.get('cached_tokens_saved', 0)} cached), "
            f"{metrics.get('completion_tokens', 0)} completion\n"
        )
        self._write_log("\n".join(log_chunk) + "\n")

    def finalize(self, diagnosis: str, scorecard: Dict[str, Any]):
        self.trace_data["final_diagnosis"] = diagnosis
        self.trace_data["scorecard"] = scorecard

        log_chunk = [
            f"======================================================================",
            f"🏁 FINAL ROOT CAUSE DIAGNOSIS:",
            f"{diagnosis}",
            f"======================================================================",
            f"📊 SCORECARD SUMMARY:",
            json.dumps(scorecard, indent=2),
            f"======================================================================\n",
        ]
        self._write_log("\n".join(log_chunk))

        # Save JSON trace
        with open(self.json_filename, "w", encoding="utf-8") as f:
            json.dump(self.trace_data, f, indent=2)

        # Update latest symlinks / files
        try:
            shutil.copyfile(self.log_filename, self.latest_log)
            shutil.copyfile(self.json_filename, self.latest_json)
        except Exception:
            pass
