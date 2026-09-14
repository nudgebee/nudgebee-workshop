#!/usr/bin/env python3
"""
===============================================================================
Building AI Agents for SRE & AIOps · Mini Investigation Agent (mini_agent.py)
===============================================================================
Lightweight, modular SRE incident investigation agent orchestrator with native
OpenAI-compatible tool calling, episodic memory, and complete audit logging.

Modular Structure:
  - config_loader.py : YAML parsing, configuration loading, prompt assembly
  - memory.py        : Episodic memory persistence (.json & .md ledgers)
  - llm_client.py    : Zero-dependency OpenAI-compatible HTTP client
  - logger.py        : Turn-by-turn audit logging and JSON trace output
  - mock_planner.py  : Deterministic offline simulation across 6 scenarios
  - tools.py         : Kubernetes, Prometheus, and deployment diagnostic tools

Diagnostic Commands:
  python3 mini_agent.py --test-tools   # Verify cluster tools health
  python3 mini_agent.py --prompt       # Inspect exact system prompt & schemas
  python3 mini_agent.py --logs         # View human-readable transcript
  python3 mini_agent.py --json         # View machine-parseable JSON trace
===============================================================================
"""

import os
import json
import time
import argparse
from typing import Dict, List, Any, Optional

from config_loader import load_config, assemble_system_prompt, MODEL_RATES
from memory import save_to_episodic_memory
from llm_client import call_openai_chat_completions
from logger import AuditLogger
from mock_planner import MockAgentPlanner
from tools import OPENAI_TOOLS, TOOL_DISPATCH, test_all_tools

CONFIG = load_config()


# ==============================================================================
# MAIN AGENT INVESTIGATION LOOP
# ==============================================================================
def run_investigation():
    ns = CONFIG["namespace"]
    model = CONFIG["model"]
    scenario = CONFIG["scenario"]
    enabled_tool_names = CONFIG["enabled_tools"]

    # Filter OpenAI tool schemas to only enabled tools
    active_tool_schemas = [t for t in OPENAI_TOOLS if t["function"]["name"] in enabled_tool_names]
    sys_prompt = assemble_system_prompt(CONFIG, scenario)
    user_prompt = CONFIG.get("initial_user_prompt", "Investigate incident").format(namespace=ns, scenario=scenario)

    logger = AuditLogger(
        log_dir=CONFIG["log_dir"],
        namespace=ns,
        system_prompt=sys_prompt,
        tool_schemas=active_tool_schemas,
        max_turns=CONFIG["max_turns"],
        config_snapshot=CONFIG
    )

    print("\n" + "="*75)
    print(f"🚨 SRE MINI AGENT LAUNCHED · NAMESPACE: '{ns}'")
    print(f"🎯 Scenario Target: {scenario} | Model: {model}")
    print(f"🛠️  Active Tools ({len(active_tool_schemas)}): {', '.join(enabled_tool_names)}")
    print(f"🧠 Caching: {CONFIG['enable_prompt_caching']} | Context Mode: {CONFIG['context_mode']}")
    print(f"💾 Episodic Memory: {'ENABLED' if CONFIG['enable_memory'] else 'DISABLED'}")
    print(f"📝 Full Audit Log: {logger.log_filename}")
    print("="*75 + "\n")

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt}
    ]

    total_prompt_tokens = 0
    total_completion_tokens = 0
    cached_tokens_saved = 0
    start_time = time.time()

    is_live_llm = (model != "mock" and bool(CONFIG.get("api_key")))
    mock_planner = MockAgentPlanner(scenario, ns, context_mode=CONFIG.get("context_mode", "filtered_regex"))
    final_diagnosis = ""
    is_final = False

    for turn in range(1, CONFIG["max_turns"] + 1):
        turn_start = time.time()
        print(f"▶ [Turn {turn}/{CONFIG['max_turns']}] Agent Planning...")

        thought = ""
        tool_call = None
        is_final = False

        if is_live_llm:
            try:
                resp = call_openai_chat_completions(
                    CONFIG["api_base"],
                    CONFIG["api_key"],
                    model,
                    messages,
                    active_tool_schemas
                )
                choice = resp["choices"][0]["message"]
                usage = resp.get("usage", {})
                active_prompt_tokens = usage.get("prompt_tokens", 800)
                completion_tok = usage.get("completion_tokens", 100)
                cached_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)

                thought = (choice.get("reasoning_content") or choice.get("content") or "").strip()
                if not thought:
                    thought = "Executing selected diagnostic tool."
                print(f"  💭 Model Reasoning: {thought}")

                raw_calls = choice.get("tool_calls", [])
                if raw_calls:
                    tc0 = raw_calls[0]
                    t_name = tc0["function"]["name"]
                    t_args = json.loads(tc0["function"]["arguments"])
                    tool_call = {"name": t_name, "args": t_args, "id": tc0["id"]}
                else:
                    is_final = True
                    final_diagnosis = thought

            except Exception as e:
                print(f"  ⚠️ Live LLM invocation error: {e}. Falling back to deterministic planner.")
                is_live_llm = False

        if not is_live_llm:
            step = mock_planner.plan_next_action(enabled_tool_names)
            thought = step.get("thought", "")
            print(f"  💭 Reasoning: {thought}")
            tool_call = step.get("tool_call")
            is_final = step.get("is_final", False)
            if is_final:
                final_diagnosis = step.get("diagnosis", "")

            # Simulated token accounting
            base_prompt_tokens = 650 + (turn * 400)
            if CONFIG["context_mode"] == "raw_80k":
                base_prompt_tokens += 12500
            elif CONFIG["context_mode"] == "filtered_regex":
                base_prompt_tokens += 450
            else:
                base_prompt_tokens += 150

            if CONFIG["enable_prompt_caching"] and turn > 1:
                cached_tokens = int(base_prompt_tokens * 0.75)
                active_prompt_tokens = base_prompt_tokens - cached_tokens
            else:
                cached_tokens = 0
                active_prompt_tokens = base_prompt_tokens
            completion_tok = 60 + len(thought.split())

        total_prompt_tokens += active_prompt_tokens
        total_completion_tokens += completion_tok
        cached_tokens_saved += cached_tokens

        turn_metrics = {
            "duration_s": time.time() - turn_start,
            "active_prompt_tokens": active_prompt_tokens,
            "cached_tokens_saved": cached_tokens,
            "completion_tokens": completion_tok,
        }

        # Final conclusion reached
        if is_final:
            logger.log_turn(turn, messages, thought, None, None, turn_metrics)
            print("\n" + "="*75)
            print("🏁 FINAL ROOT CAUSE INVESTIGATION COMPLETE")
            print("="*75)
            print(f"\n{final_diagnosis}\n")
            break

        # Execute Tool Call
        observation = ""
        if tool_call:
            t_name = tool_call["name"]
            t_args = tool_call.get("args", {})
            print(f"  ⚡ Tool Dispatch: {t_name}({json.dumps(t_args)})")

            handler = TOOL_DISPATCH.get(t_name)
            if handler:
                if t_name == "search_incident_history":
                    t_args["enable_memory"] = CONFIG.get("enable_memory", False)
                observation = handler(t_args)
            else:
                observation = f"Error: Tool '{t_name}' is not registered."

            display_obs = observation.replace("\n", " ")
            if len(display_obs) > 160:
                display_obs = display_obs[:160] + "..."
            print(f"  📥 Observation: {display_obs}")
            print(f"  ⏱️  Turn Duration: {time.time() - turn_start:.2f}s\n")

            # Append to message trajectory
            if "id" in tool_call:
                assistant_entry = {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tool_call["id"],
                        "type": "function",
                        "function": {"name": t_name, "arguments": json.dumps(t_args)}
                    }]
                }
                if thought and thought != "Executing selected diagnostic tool.":
                    assistant_entry["content"] = thought
                messages.append(assistant_entry)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": t_name,
                    "content": str(observation)
                })
            else:
                messages.append({"role": "assistant", "content": f"Action: {t_name}\nArgs: {json.dumps(t_args)}"})
                messages.append({"role": "user", "content": f"Observation: {observation}"})

        turn_metrics["duration_s"] = time.time() - turn_start
        logger.log_turn(turn, messages, thought, tool_call, observation, turn_metrics)

    # Final synthesis turn if max_turns was reached without explicit stop
    if not is_final:
        print("\n▶ [Final Synthesis] Maximum turns reached. Formulating definitive root cause...")
        synth_messages = list(messages) + [
            {"role": "user", "content": "Based on all the telemetry, logs, and deployment data you have inspected, declare the definitive ROOT CAUSE IDENTIFIED, key evidence, and remediation actions."}
        ]
        if is_live_llm:
            try:
                resp = call_openai_chat_completions(
                    CONFIG["api_base"],
                    CONFIG["api_key"],
                    model,
                    synth_messages,
                    tools=[]  # Pure text synthesis without tools
                )
                choice = resp["choices"][0]["message"]
                final_diagnosis = (choice.get("reasoning_content") or choice.get("content") or "").strip()
                usage = resp.get("usage", {})
                total_prompt_tokens += usage.get("prompt_tokens", 800)
                total_completion_tokens += usage.get("completion_tokens", 100)
            except Exception as e:
                final_diagnosis = f"Investigation concluded after {CONFIG['max_turns']} turns. Telemetry gathered in log."
        else:
            final_diagnosis = f"Investigation concluded after {CONFIG['max_turns']} turns."

        print("\n" + "="*75)
        print("🏁 FINAL ROOT CAUSE INVESTIGATION COMPLETE")
        print("="*75)
        print(f"\n{final_diagnosis}\n")

    # Persist investigation resolution to episodic memory store
    save_to_episodic_memory(final_diagnosis, scenario, ns, model)

    # Scorecard calculation
    total_elapsed = time.time() - start_time
    rate = MODEL_RATES.get(model, 0.0003)
    est_cost = ((total_prompt_tokens + total_completion_tokens) / 1000.0) * rate
    cached_savings_dollars = (cached_tokens_saved / 1000.0) * (rate * 0.75)

    # Ground-truth accuracy check per scenario
    d_lower = final_diagnosis.lower()
    if scenario == "badDeploy1405":
        verified = "checkout" in d_lower and any(k in d_lower for k in ["a7f39b1", "deploy", "timeout", "5000ms", "rollback"])
    elif scenario == "episodicRecurrence":
        verified = any(k in d_lower for k in ["inc-4092", "14 march", "connection", "starvation", "pgbouncer"])
    elif scenario == "emailMemoryLeak":
        verified = "email" in d_lower and any(k in d_lower for k in ["memory", "leak", "oom", "137"])
    elif scenario == "postgresSlow":
        verified = any(k in d_lower for k in ["postgres", "database", "latency", "pg_sleep", "delay", "slow"])
    elif scenario == "poisonedEntity":
        verified = any(k in d_lower for k in ["abstain", "does not exist", "not exist", "unresolv", "not found"])
    else:  # postgresFailure
        verified = any(k in d_lower for k in ["postgres", "astronomy-db", "starvation", "connection", "pool"])

    scorecard = {
        "namespace": ns,
        "model": model,
        "scenario": scenario,
        "turns_used": f"{turn} / {CONFIG['max_turns']}",
        "total_prompt_tokens": total_prompt_tokens,
        "cached_tokens_saved": cached_tokens_saved,
        "completion_tokens": total_completion_tokens,
        "total_latency_seconds": round(total_elapsed, 2),
        "estimated_api_cost": round(est_cost, 5),
        "cached_savings_dollars": round(cached_savings_dollars, 5),
        "accuracy_verified": verified,
    }

    logger.finalize(final_diagnosis, scorecard)

    result_badge = "✅ SUCCESS (Root Cause Identified)" if verified else "⚠️ PARTIAL (Needs Further Evidence)"

    print("="*75)
    print("📊 RUN SCORECARD (Logged to file and ready for room scoreboard)")
    print("="*75)
    print(f"├── Assigned Namespace    : {ns}")
    print(f"├── Model Architecture    : {model}")
    print(f"├── Scenario Evaluated    : {scenario}")
    print(f"├── Total Turns Used      : {turn} / {CONFIG['max_turns']}")
    print(f"├── Active Prompt Tokens  : {total_prompt_tokens:,} tokens")
    print(f"├── Cached Tokens Saved   : {cached_tokens_saved:,} tokens (Saved ~${cached_savings_dollars:.5f})")
    print(f"├── Completion Tokens     : {total_completion_tokens:,} tokens")
    print(f"├── Total Latency         : {total_elapsed:.2f} seconds")
    print(f"├── Estimated API Cost    : ${est_cost:.5f}")
    print(f"├── Execution Log         : {logger.log_filename}")
    print(f"└── Ground Truth Result   : {result_badge}")
    print("="*75)
    print("👉 View full readable log: python3 mini_agent.py --logs")
    print("👉 Inspect prompt & tools: python3 mini_agent.py --prompt")
    print("👉 Enter metrics into:    https://nudgebee-workshop.pollux.in/scoreboard.html\n")


# ==============================================================================
# CLI HANDLERS
# ==============================================================================
def cli_test_tools(namespace: str):
    print(f"\n🔍 RUNNING CLUSTER TOOLS HEALTH CHECK AGAINST NAMESPACE: '{namespace}'...")
    print("-" * 75)
    results = test_all_tools(namespace)
    for name, passed, output in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        clean_out = output.replace("\n", " ")
        if len(clean_out) > 80:
            clean_out = clean_out[:80] + "..."
        print(f"{status} | {name:<18} | {clean_out}")
    print("-" * 75 + "\n")


def cli_view_prompt():
    scen = CONFIG.get("scenario", "badDeploy1405")
    sys_p = assemble_system_prompt(CONFIG, scen)
    user_p = CONFIG.get("initial_user_prompt", "").format(
        namespace=CONFIG.get("namespace", "group-1"),
        scenario=scen
    )
    print("\n" + "="*75)
    print("📌 AGENT SYSTEM PROMPT:")
    print("="*75)
    print(sys_p.strip())
    print("\n" + "="*75)
    print("💬 INITIAL USER PROMPT (TASK FRAMING):")
    print("="*75)
    print(user_p.strip())
    print("\n" + "="*75)
    print("🛠️ REGISTERED OPENAI FUNCTION-CALLING TOOL SCHEMAS:")
    print("="*75)
    print(json.dumps(OPENAI_TOOLS, indent=2))
    print("\n")


def cli_view_log():
    latest = os.path.join(CONFIG["log_dir"], "investigation_latest.log")
    if not os.path.exists(latest):
        print("No previous investigation logs found. Run 'python3 mini_agent.py' first.")
        return
    with open(latest, "r", encoding="utf-8") as f:
        print(f.read())


def cli_view_json():
    latest = os.path.join(CONFIG["log_dir"], "investigation_latest.json")
    if not os.path.exists(latest):
        print("No previous investigation JSON trace found. Run 'python3 mini_agent.py' first.")
        return
    with open(latest, "r", encoding="utf-8") as f:
        print(f.read())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SRE Mini Investigation Agent")
    parser.add_argument("--test-tools", action="store_true", help="Run live health check on all cluster tools")
    parser.add_argument("--prompt", "--view-prompt", action="store_true", help="Inspect exact system prompt and OpenAI tool schemas")
    parser.add_argument("--logs", "--view-log", action="store_true", help="Print the latest readable execution log")
    parser.add_argument("--json", "--view-json", action="store_true", help="Print the latest JSON execution trace")
    parser.add_argument("--model", type=str, help="Override LLM model (e.g. 'gemini-3.8-flash', 'NVIDIA-Nemotron-3-Nano-30B-A3B-BF16', 'mock')")
    parser.add_argument(
        "--scenario",
        type=str,
        choices=["badDeploy1405", "episodicRecurrence", "postgresFailure", "emailMemoryLeak", "postgresSlow", "poisonedEntity"],
        help="Override scenario target"
    )
    parser.add_argument("--namespace", type=str, help="Override Kubernetes namespace")
    args = parser.parse_args()

    if args.test_tools:
        cli_test_tools(args.namespace or CONFIG["namespace"])
    elif args.prompt:
        cli_view_prompt()
    elif args.logs:
        cli_view_log()
    elif args.json:
        cli_view_json()
    else:
        if args.model:
            CONFIG["model"] = args.model
        if args.scenario:
            CONFIG["scenario"] = args.scenario
        if args.namespace:
            CONFIG["namespace"] = args.namespace
        run_investigation()
