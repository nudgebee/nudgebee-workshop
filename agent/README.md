# 🤖 Autonomous SRE Incident Investigation Agent

A transparent, modular, and configurable AI SRE investigation agent designed for the **Building AI Agents for SRE & AIOps** 4-hour masterclass.

Attendees interact entirely through declarative configuration files (`config.yaml` and `prompts.yaml`) — **no Python coding required**.

---

## ⚡ 2-Minute Quickstart

### 1. Verify Cluster Tools Health
```bash
python3 mini_agent.py --test-tools
```
*Runs instant read-only diagnostics across Kubernetes events, Prometheus PromQL, container logs, service topology, deployment rollouts, and episodic memory.*

### 2. Run the Autonomous Investigation
```bash
python3 mini_agent.py
```
*Launches the multi-turn ReAct investigation loop. The agent reasons, invokes diagnostic tools, respects security confirmation gates, isolates root cause, and generates a run scorecard.*

### 3. Inspect Full Audit Logs & Execution Traces
```bash
# View complete step-by-step human-readable transcript:
python3 mini_agent.py --logs

# View complete machine-parseable JSON execution trace:
python3 mini_agent.py --json

# Inspect active system prompt, guardrails, and registered OpenAI tool schemas:
python3 mini_agent.py --prompt
```

---

## 🏗️ Modular Architecture

The agent is organized into clean, single-responsibility modules:

```
workshop-tools/agent/
├── config.yaml          # Attendee levers: model, enabled tools, scenario, memory, caching
├── prompts.yaml         # Agent persona, security guardrails, reasoning modes, scenario hints
├── config_loader.py     # Zero-dep YAML parser, configuration loader, prompt assembler
├── memory.py            # Episodic memory persistence (.json store & .md ledger)
├── llm_client.py        # Zero-dep OpenAI-compatible HTTP client (/v1/chat/completions)
├── logger.py            # AuditLogger: turn-by-turn .log transcripts & .json traces
├── mock_planner.py      # Deterministic multi-turn simulation across all 6 scenarios
├── tools.py             # 7 cluster diagnostic tools + OpenAPI function-calling schemas
├── mini_agent.py        # Slim orchestrator (~250 lines): ReAct loop & CLI dispatch
├── memory/
│   ├── episodic_memory.json   # Machine-readable post-mortem store
│   └── episodic_memory.md     # Human-readable incident ledger
└── logs/
    ├── investigation_latest.log     # Latest human-readable execution log
    ├── investigation_latest.json    # Latest JSON trace with scorecard metrics
    └── investigation_<timestamp>_<namespace>.log
```

---

## 🎯 The 6 Workshop Scenarios

Select any scenario in `config.yaml` (`scenario: "<name>"`) or override via CLI flag (`python3 mini_agent.py --scenario <name>`):

| Scenario Key | Masterclass Module | Incident Archetype | Expected Root Cause & Evidence |
| :--- | :--- | :--- | :--- |
| **`badDeploy1405`** | **Module 6 & Session 2C**<br/>*(Add a Tool, Change the Answer)* | Checkout latency spike & 5xx cascading to Storefront | Correlate with `get_deploy_history`. Commit `a7f39b1` bumped `payment_client_timeout` from `500ms` to `5000ms`, exhausting worker threads. Roll back to Rev 2 via `ask_human_approval`. |
| **`episodicRecurrence`** | **Module 8 & Session 2A**<br/>*(Episodic Memory On vs Off)* | Recurrent connection pool starvation on `astronomy-db` | Turn 1 (`enable_memory: false`): Fresh investigation. Turn 2 (`enable_memory: true`): Recalls `INC-4092` from 14 March via `search_incident_history`, comparing symptoms and prior fix. |
| **`postgresFailure`** | **Module 2 & 3**<br/>*(Small vs Frontier & Tool Ceiling)* | Database pool exhaustion / unreachable PostgreSQL | Product-catalog throws gRPC status 13 `INTERNAL`. Saturated connection slots trigger rollout restart via safety gate. |
| **`emailMemoryLeak`** | **Module 4**<br/>*(Context & Telemetry Needle)* | Kernel OOMKill Exit Code 137 on `email` service | Calculate 10-minute soak derivative `deriv(container_memory_working_set_bytes[5m]) > 0` to prove progressive heap leak vs normal spike. |
| **`postgresSlow`** | **Module 4**<br/>*(Latency Without Errors)* | Artificial `pg_sleep` injected in database queries | Pods remain `Running 1/1` with zero 500 errors. Agent analyzes p99 query duration percentiles to isolate slow queries. |
| **`poisonedEntity`** | **Module 3 & Session 4**<br/>*(Red-Teaming & Hallucinations)* | Bogus alert citing fictitious `billing-worker` | Agent inspects topology, finds entity non-existent in cluster, and enforces **strict abstention policy** rather than hallucinating. |

---

## 🎛️ Workshop Levers (No Python Coding Required)

### Levers in `config.yaml`

| Lever | Key | Options | Experiment & Learning Objective |
| :--- | :--- | :--- | :--- |
| **1. Model Selection** | `model` | `"gemini-3.8-flash"`<br/>`"qwen/qwen3-235b-a22b-instruct-2507-maas"`<br/>`"NVIDIA-Nemotron-3-Nano-30B-A3B-BF16"`<br/>`"mock"` | **Module 2**: Frontier vs open 235B vs small 30B vs offline simulation. Compare token costs, reasoning depth, and tool selection precision. |
| **2. Tool Deprivation** | `enabled_tools` | Comment out tools under `enabled_tools:` list | **Module 3 & 6**: Observe how the agent behaves when deprived of `get_deploy_history`, `query_pod_logs`, or `inspect_topology`. |
| **3. Context Window** | `context_mode` | `"structured_summary"` (2k tokens)<br/>`"filtered_regex"` (8k tokens)<br/>`"raw_80k"` (80k uncurated dump) | **Module 4**: Test lost-in-the-middle needle retrieval and observe token cost multipliers. |
| **4. Prompt Caching** | `enable_prompt_caching` | `true` / `false` | **Module 4 & 8**: Measure prompt cache hit rates and dollar savings on subsequent turns. |
| **5. Episodic Memory** | `enable_memory` | `false` (Turn 1: Record)<br/>`true` (Turn 2: Recall) | **Module 8**: Turn 1 writes post-mortems to `memory/`; Turn 2 prompts agent to recall past incidents via `search_incident_history`. |
| **6. Turn Budget** | `max_turns` | Integer (default: `10`) | Control investigation loop budget before forcing final root cause synthesis. |

### Levers in `prompts.yaml`

| Module | Key | Options | Experiment & Learning Objective |
| :--- | :--- | :--- | :--- |
| **Module 1** | `system_prompt` | Markdown text | Modify agent persona, diagnostic rigor, and hypothesis testing guidelines. |
| **Module 4 & 6** | `reasoning_mode` | `"deep_rca"`<br/>`"quick_triage"`<br/>`"json_structured"` | Compare 4-step root cause analysis vs 2-turn speed triage vs strict JSON output. |
| **Module 7** | `security_guardrails` | Markdown text | Test read-only policy enforcement and human confirmation gate (`ask_human_approval`). |

---

## 🛠️ The 7 Cluster Diagnostic Tools

All tools are read-only and execute live commands against the target cluster:

1. **`query_prometheus`**: Executes instant PromQL queries against Prometheus (via direct endpoint or K8s API proxy fallback).
2. **`get_k8s_events`**: Fetches and filters recent Kubernetes events for pod restarts, CrashLoopBackOff, and OOMKills.
3. **`query_pod_logs`**: Fetches container stdout/stderr logs with context modes (`filtered_regex`, `structured_summary`, or `raw_80k`).
4. **`inspect_topology`**: Queries the 1-hop microservice knowledge graph for upstream callers and downstream dependencies.
5. **`ask_human_approval`**: Enforces a Human-in-the-Loop security gate before any mutating remediation action (`kubectl rollout undo`, etc.).
6. **`get_deploy_history`**: Inspects `kubectl rollout history` and deployment revision manifests for commit hashes, authors, and config diffs.
7. **`search_incident_history`**: Searches episodic memory (`memory/episodic_memory.json` & `.md`) for matching past post-mortems and prior fixes.

---

## 📊 Run Scorecard & Leaderboard

At the end of every investigation, a structured scorecard is printed and logged to `logs/investigation_latest.json`:

```
===========================================================================
📊 RUN SCORECARD (Logged to file and ready for room scoreboard)
===========================================================================
├── Assigned Namespace    : group-1
├── Model Architecture    : gemini-3.8-flash
├── Scenario Evaluated    : badDeploy1405
├── Total Turns Used      : 10 / 10
├── Active Prompt Tokens  : 31,416 tokens
├── Cached Tokens Saved   : 0 tokens (Saved ~$0.00000)
├── Completion Tokens     : 3,544 tokens
├── Total Latency         : 39.51 seconds
├── Estimated API Cost    : $0.01049
├── Execution Log         : logs/investigation_20260914_100550_group-1.log
└── Ground Truth Result   : ✅ SUCCESS (Root Cause Identified)
===========================================================================
```

Enter your metrics into the live room scoreboard:
👉 **[Open Room Scoreboard](https://nudgebee-workshop.pollux.in/scoreboard.html)**
