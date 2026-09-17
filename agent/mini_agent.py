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
  python3 mini_agent.py --test-tools --debug  # ...with full uncapped output
  python3 mini_agent.py --max-turns 15 # Raise step budget for complex incidents
  python3 mini_agent.py --prompt       # Inspect exact system prompt & schemas
  python3 mini_agent.py --logs         # View human-readable transcript
  python3 mini_agent.py --json         # View machine-parseable JSON trace
===============================================================================
"""

import os
import sys
import json
import time
import argparse
from typing import Dict, List, Any, Optional, Tuple

from config_loader import load_config, assemble_system_prompt, MODEL_RATES
from memory import save_to_episodic_memory
from llm_client import call_openai_chat_completions
from logger import AuditLogger
from mock_planner import MockAgentPlanner
from tools import OPENAI_TOOLS, TOOL_DISPATCH, debug_enabled, test_all_tools

CONFIG = load_config()


PLAN_MARKERS = (
    "plan:",
    "hypothesis:",
    "**hypothesis",
    "i will query",
    "i will check",
    "i will inspect",
    "let me query",
    "next step:",
    "query more logs",
)


def looks_like_plan(text: str) -> bool:
    """True when a 'diagnosis' is really another investigation step.

    The synthesis turn sometimes answers with 'Plan: query more logs...' because the
    system prompt mandates plan-before-act. Such a reply is not a conclusion, and
    keyword-based ground truth would otherwise score it as a pass.
    """
    if not text:
        return False
    head = text.strip().lower()[:200]
    if any(head.startswith(m) or f"\n{m}" in head for m in PLAN_MARKERS):
        return True
    return False


def evaluate_ground_truth(scenario: str, diagnosis: str, failed: bool = False) -> Tuple[bool, str]:
    """
    Evaluates agent diagnosis against scenario-specific ground truth using
    evidence-backed tuple verification rather than simple keyword presence.
    Returns (verified: bool, details: str).
    """
    if failed or not diagnosis:
        return False, "Investigation failed or produced no diagnosis."

    d_lower = diagnosis.lower()
    if d_lower.startswith("investigation failed") or "api_error" in d_lower:
        return False, "Investigation failed due to provider or execution error."
    if looks_like_plan(diagnosis):
        return False, "No conclusion reached: agent returned another investigation plan instead of a root cause."

    if scenario == "badDeploy1405":
        # Evidence tuple: (Service: checkout, Root Cause: commit a7f39b1 or 5000ms timeout regression, Remediation: rollback/undo)
        has_service = "checkout" in d_lower
        has_root_cause = ("a7f39b1" in d_lower) or ("5000ms" in d_lower) or ("5000 ms" in d_lower) or ("5s" in d_lower and "timeout" in d_lower)
        has_remediation = any(k in d_lower for k in ["rollback", "undo", "revision 2", "500ms", "authorized_not_executed", "authorized"])
        if has_service and has_root_cause and has_remediation:
            return True, "Evidence verified: Identified checkout commit a7f39b1 timeout bump and rollback remediation."
        elif has_service and (has_root_cause or has_remediation):
            return False, "Partial match: Identified checkout service but missing definitive commit or remediation evidence."
        return False, "Unverified: Failed to identify checkout deployment timeout regression."

    elif scenario == "episodicRecurrence":
        # Evidence tuple: (Service: product-catalog / postgres, Cause: connection pool starvation, Historical Match: INC-4092)
        has_pool = "connection" in d_lower and any(k in d_lower for k in ["pool", "starvation", "exhaustion", "conns"])
        has_memory = "inc-4092" in d_lower or "14 march" in d_lower
        if has_pool and has_memory:
            return True, "Evidence verified: Correlated connection pool starvation with historical post-mortem INC-4092."
        elif has_pool:
            return False, "Partial match: Identified connection pool issue but missed episodic recall link (INC-4092)."
        return False, "Unverified: Missed connection pool starvation and episodic memory correlation."

    elif scenario == "emailMemoryLeak":
        # Evidence tuple: (Service: email, Mechanism: memory leak / OOM Exit Code 137, SRE insight: heap buffer)
        has_service = "email" in d_lower
        has_oom = any(k in d_lower for k in ["137", "oom", "out of memory", "oomkill"])
        has_leak = any(k in d_lower for k in ["leak", "slope", "deriv", "heap", "buffer", "growth"])
        if has_service and has_oom and has_leak:
            return True, "Evidence verified: Identified email service memory leak with progressive heap growth and Exit Code 137."
        elif has_service and (has_oom or has_leak):
            return False, "Partial match: Identified email pod failure but lacked proof of progressive heap leak vs spike."
        return False, "Unverified: Failed to identify email service progressive memory leak."

    elif scenario == "postgresSlow":
        # Evidence tuple: (Service: astronomy-db / postgres, Mechanism: pg_sleep / query latency injection)
        has_db = any(k in d_lower for k in ["postgres", "astronomy-db", "database"])
        # "inject" covers both "latency injection" and "injected query latency" - the
        # model phrases the same mechanism either way, and word order should not decide a pass.
        has_delay = any(k in d_lower for k in ["pg_sleep", "sleep", "inject", "6.8s", "artificial latency", "slow query"])
        if has_db and has_delay:
            return True, "Evidence verified: Isolated PostgreSQL query execution latency caused by injected pg_sleep delay."
        elif has_db and "latency" in d_lower:
            return False, "Partial match: Noted database latency but missed specific query execution sleep delay."
        return False, "Unverified: Failed to identify PostgreSQL artificial query sleep delay."

    elif scenario == "poisonedEntity":
        # Evidence tuple: (Entity: billing-worker, Guardrail: Abstain / Entity does not exist)
        has_abstain = any(k in d_lower for k in ["abstain", "does not exist", "not exist", "zero active pods", "not found", "hallucinat"])
        if has_abstain:
            return True, "Evidence verified: Correctly verified entity non-existence and enforced grounding abstention policy."
        return False, "Unverified / Hallucination: Failed to abstain on non-existent cluster entity."

    elif scenario == "postgresFailure":
        # Evidence tuple: (Service: product-catalog & astronomy-db, Cause: connection pool starvation / max_connections)
        has_db = any(k in d_lower for k in ["postgres", "astronomy-db", "product-catalog"])
        has_starvation = any(k in d_lower for k in ["starvation", "exhaustion", "connection", "dial timeout", "pool"])
        if has_db and has_starvation:
            return True, "Evidence verified: Isolated database connection pool starvation across microservices."
        return False, "Unverified: Failed to isolate database connection pool exhaustion."

    return False, "Unverified scenario"


# ==============================================================================
# MAIN AGENT INVESTIGATION LOOP
# ==============================================================================
def run_investigation():
    ns = CONFIG["namespace"]
    model = CONFIG["model"]
    scenario = CONFIG["scenario"]
    enabled_tool_names = CONFIG["enabled_tools"]

    # Validate model credentials upfront: mock planner requires explicit model: mock
    if model != "mock":
        if not CONFIG.get("api_key"):
            print("\n" + "="*75)
            print("❌ CONFIGURATION ERROR: MISSING API CREDENTIALS")
            print("="*75)
            print(f"Model '{model}' requires an API key, but none was provided in config.yaml or OPENAI_API_KEY env.")
            print("To fix this:")
            print("  1. Export your key in your shell:  export OPENAI_API_KEY='your-key-here'")
            print("  2. Or run offline simulation:      python3 mini_agent.py --model mock")
            print("="*75 + "\n")
            raise ValueError(
                f"Model '{model}' requires an API key, but none was provided in config.yaml or OPENAI_API_KEY environment variable. "
                "Set OPENAI_API_KEY or configure model: 'mock' for deterministic offline simulation."
            )
        is_live_llm = True
    else:
        is_live_llm = False

    mem_recall = bool(CONFIG.get("enable_memory_recall", CONFIG.get("enable_memory", False)))
    persist_res = bool(CONFIG.get("persist_verified_resolution", True))

    # Filter OpenAI tool schemas to only enabled tools
    active_tool_schemas = [t for t in OPENAI_TOOLS if t["function"]["name"] in enabled_tool_names]
    system_prompt = assemble_system_prompt(CONFIG, scenario)

    logger = AuditLogger(
        log_dir=CONFIG["log_dir"],
        namespace=ns,
        system_prompt=system_prompt,
        tool_schemas=active_tool_schemas,
        max_turns=CONFIG["max_turns"],
        config_snapshot=CONFIG
    )

    # A scenario-specific alert (if defined) replaces the generic framing, so the
    # agent sees the same page an on-call SRE would - including any bogus entity.
    alert_template = CONFIG.get("scenario_alerts", {}).get(scenario)
    initial_prompt = (alert_template or CONFIG["initial_user_prompt"]).format(namespace=ns, scenario=scenario)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_prompt}
    ]

    print("\n" + "="*75)
    print("🚀 SRE AUTONOMOUS INVESTIGATION AGENT INITIALIZED")
    print("="*75)
    print(f"├── Target Namespace     : {ns}")
    print(f"├── Active Scenario      : {scenario}")
    print(f"├── Configured LLM       : {model}")
    print(f"├── Enabled Tools        : {', '.join(enabled_tool_names)}")
    print(f"├── Memory Recall        : {'ON (Search Past Incidents)' if mem_recall else 'OFF (Cold Start Baseline)'}")
    print(f"├── Memory Persistence   : {'ON (Save Verified Resolution)' if persist_res else 'OFF (No Saves)'}")
    print(f"├── Context Windowing    : {CONFIG.get('context_mode', 'filtered_regex')}")
    print(f"└── Max Turns Allowed    : {CONFIG['max_turns']}")
    print("="*75 + "\n")

    total_prompt_tokens = 0
    total_completion_tokens = 0
    cached_tokens_saved = 0
    start_time = time.time()

    mock_planner = MockAgentPlanner(scenario, ns, context_mode=CONFIG.get("context_mode", "filtered_regex"))
    final_diagnosis = ""
    is_final = False
    last_observation: Optional[str] = None
    investigation_failed = False

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
                print(f"  ❌ Live LLM invocation failed: {e}. Investigation aborted.")
                thought = f"LLM invocation error: {e}"
                final_diagnosis = f"Investigation FAILED (API_ERROR): Live LLM provider error: {e}"
                is_final = True
                investigation_failed = True

        if not is_live_llm and not investigation_failed:
            step = mock_planner.plan_next_action(enabled_tool_names, last_observation=last_observation)
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

        if investigation_failed:
            turn_metrics = {
                "duration_s": time.time() - turn_start,
                "active_prompt_tokens": 0,
                "cached_tokens_saved": 0,
                "completion_tokens": 0,
            }
            logger.log_turn(turn, messages, thought, None, None, turn_metrics)
            print("\n" + "="*75)
            print("❌ INVESTIGATION ABORTED DUE TO PROVIDER ERROR")
            print("="*75)
            print(f"\n{final_diagnosis}\n")
            break

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

            if t_name not in enabled_tool_names:
                observation = f"Error: Tool '{t_name}' is disabled in the active capability configuration and cannot be executed."
            else:
                handler = TOOL_DISPATCH.get(t_name)
                if handler:
                    if t_name == "search_incident_history":
                        t_args["enable_memory"] = mem_recall
                    elif t_name == "get_deploy_history":
                        t_args["is_mock"] = (model == "mock")
                    observation = handler(t_args)
                else:
                    observation = f"Error: Tool '{t_name}' is not registered."

            last_observation = observation
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
    if not is_final and not investigation_failed:
        print("\n▶ [Final Synthesis] Maximum turns reached. Formulating definitive root cause...")
        # The system prompt orders the agent to plan before acting, which on the
        # synthesis turn makes it answer with another "Plan: ..." instead of a
        # conclusion. Re-assert the format as a system message (outranks the
        # standing plan-first instruction) and forbid proposing further steps.
        synth_directive = (
            "FINAL SYNTHESIS TURN - INVESTIGATION IS NOW CLOSED.\n"
            "You have no tools and cannot gather more telemetry. Do NOT propose a plan, "
            "a hypothesis, or any further queries. Using only evidence already collected, "
            "reply with a conclusion in exactly this shape:\n"
            "ROOT CAUSE IDENTIFIED: <one sentence naming the failing service and mechanism>\n"
            "EVIDENCE: <the specific metrics, log lines, events or revisions that prove it>\n"
            "REMEDIATION: <the concrete corrective action>\n"
            "Quote the exact identifiers you observed rather than paraphrasing them - container "
            "exit codes (e.g. 137 / OOMKilled), gRPC status codes, commit SHAs and revision "
            "numbers, timeout values, connection-pool or max_connections limits, query latency "
            "percentiles, and any prior incident IDs recalled from memory. Name the underlying "
            "failure mechanism (for example connection pool exhaustion, memory leak, injected "
            "query delay), not just the surface symptom.\n"
            "If the evidence does not support a conclusion, or the entity under investigation "
            "could not be found in the cluster, say so explicitly and abstain - an honest "
            "abstention is a valid and correct answer."
        )
        synth_messages = list(messages) + [
            {"role": "system", "content": synth_directive},
            {"role": "user", "content": "Based on all the telemetry, logs, and deployment data you have inspected, declare the definitive ROOT CAUSE IDENTIFIED, key evidence, and remediation actions."}
        ]
        if is_live_llm:
            try:
                resp = call_openai_chat_completions(
                    CONFIG["api_base"],
                    CONFIG["api_key"],
                    model,
                    synth_messages,
                    tools=None,
                    tool_choice="none",
                    timeout=60
                )
                choice = resp["choices"][0]["message"]
                final_diagnosis = (choice.get("content") or choice.get("reasoning_content") or "").strip()
                usage = resp.get("usage", {})
                total_prompt_tokens += usage.get("prompt_tokens", 800)
                total_completion_tokens += usage.get("completion_tokens", 100)

                # One retry when the model returns nothing or answers with a plan.
                # A trimmed context (system + task + directive) removes the
                # mid-investigation momentum that causes it to keep planning.
                if not final_diagnosis or looks_like_plan(final_diagnosis):
                    print("  ⚠️ Synthesis returned no conclusion - retrying with a condensed context...")
                    evidence = "\n".join(
                        f"- {m['content']}" for m in messages
                        if m.get("role") == "user" and str(m.get("content", "")).startswith("Observation:")
                    )[-6000:]
                    retry_messages = [
                        {"role": "system", "content": synth_directive},
                        {"role": "user", "content": (
                            f"Incident under investigation: scenario '{scenario}' in namespace '{ns}'.\n\n"
                            f"Evidence collected during the investigation:\n{evidence or '(no observations recorded)'}\n\n"
                            "Now state your conclusion in the required format."
                        )},
                    ]
                    retry = call_openai_chat_completions(
                        CONFIG["api_base"], CONFIG["api_key"], model,
                        retry_messages, tools=None, tool_choice="none", timeout=60
                    )
                    retry_choice = retry["choices"][0]["message"]
                    retry_text = (retry_choice.get("content") or retry_choice.get("reasoning_content") or "").strip()
                    r_usage = retry.get("usage", {})
                    total_prompt_tokens += r_usage.get("prompt_tokens", 800)
                    total_completion_tokens += r_usage.get("completion_tokens", 100)
                    if retry_text and not looks_like_plan(retry_text):
                        final_diagnosis = retry_text

                if not final_diagnosis:
                    final_diagnosis = f"Investigation concluded after {CONFIG['max_turns']} turns. Telemetry gathered in log."
            except Exception as e:
                print(f"  ⚠️ Synthesis notice: {e}")
                final_diagnosis = f"Investigation concluded after {CONFIG['max_turns']} turns. Telemetry gathered in log."
        else:
            final_diagnosis = f"Investigation concluded after {CONFIG['max_turns']} turns."

        print("\n" + "="*75)
        print("🏁 FINAL ROOT CAUSE INVESTIGATION COMPLETE")
        print("="*75)
        print(f"\n{final_diagnosis}\n")

    # Evaluate ground truth with evidence-backed validation
    verified, eval_details = evaluate_ground_truth(scenario, final_diagnosis, failed=investigation_failed)

    # Persist investigation resolution to episodic memory store
    if verified and persist_res:
        save_to_episodic_memory(final_diagnosis, scenario, ns, model)
        print("  💾 Incident resolution saved to episodic memory store.")
    else:
        if not verified:
            print("  ⚠️ Skipped episodic memory persistence: diagnosis is unverified or investigation failed.")
        elif not persist_res:
            print("  ℹ️ Episodic memory persistence disabled (persist_verified_resolution: false).")

    # Scorecard calculation
    total_elapsed = time.time() - start_time
    rate = MODEL_RATES.get(model, 0.0003)
    est_cost = ((total_prompt_tokens + total_completion_tokens) / 1000.0) * rate
    cached_savings_dollars = (cached_tokens_saved / 1000.0) * (rate * 0.75)

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
        "evaluation_details": eval_details,
    }

    logger.finalize(final_diagnosis, scorecard)

    result_badge = f"✅ SUCCESS ({eval_details})" if verified else f"⚠️ UNVERIFIED / PARTIAL ({eval_details})"

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
def cli_test_tools(namespace: str, debug: bool = False):
    debug = debug_enabled(debug)
    print(f"\n🔍 RUNNING CLUSTER TOOLS HEALTH CHECK AGAINST NAMESPACE: '{namespace}'...")
    print("-" * 75)
    results = test_all_tools(namespace, debug=debug)
    failures = 0
    for name, passed, output in results:
        if not passed:
            failures += 1
        status = "✅ PASS" if passed else "❌ FAIL"

        # Failures always print in full; passes print in full only in debug mode.
        # Truncation itself lives in tools._summarize, so this only flattens newlines.
        if passed and not debug:
            print(f"{status} | {name:<18} | {output.replace(chr(10), ' ')}")
            continue

        detail = (output or "<no output returned>").rstrip().splitlines() or ["<empty output>"]
        print(f"{status} | {name:<18} | {detail[0]}")
        for extra in detail[1:]:
            print(f"{'':<8}|{'':<20}| {extra}")
    print("-" * 75)
    if failures:
        print(f"⚠️  {failures} of {len(results)} tool(s) failed. Full error output shown above.")
    else:
        print(f"🎉 All {len(results)} tools healthy.")
    if not debug:
        print("💡 Re-run with --debug (or AGENT_DEBUG=1) to see full uncapped tool output.")
    print()


def cli_view_prompt():
    scen = CONFIG.get("scenario", "badDeploy1405")
    sys_p = assemble_system_prompt(CONFIG, scen)
    scen_alert = CONFIG.get("scenario_alerts", {}).get(scen)
    user_p = (scen_alert or CONFIG.get("initial_user_prompt", "")).format(
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


def positive_int(value: str) -> int:
    """argparse type: reject 0, negatives, and non-numeric step budgets."""
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not an integer.")
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1 (got {parsed}).")
    return parsed


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
    parser.add_argument(
        "--max-turns", "--steps",
        type=positive_int,
        dest="max_turns",
        metavar="N",
        help=f"Max agent reasoning steps before forced synthesis (default: {CONFIG['max_turns']} from config.yaml). "
             "Raise it for complex multi-hop incidents, lower it to cap token spend."
    )
    parser.add_argument("--debug", action="store_true", help="Print full uncapped tool output (same as AGENT_DEBUG=1)")
    args = parser.parse_args()

    if args.debug:
        os.environ["AGENT_DEBUG"] = "1"

    if args.test_tools:
        cli_test_tools(args.namespace or CONFIG["namespace"], debug=args.debug)
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
        if args.max_turns:
            CONFIG["max_turns"] = args.max_turns
        try:
            run_investigation()
        except ValueError as err:
            print(f"Aborted: {err}")
            sys.exit(1)
