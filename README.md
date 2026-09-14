# 🐝 Building AI Agents for SRE & AIOps

> **4-Hour Interactive Systems Engineering Masterclass**  
> *8 Modules · 1 Running Scenario · Live Room Scoreboard · Autonomous SRE Agent*

Welcome to the **NudgeBee SRE & AIOps Agent Workshop** repository! This repository contains the complete curriculum, web-based operational tools, and an autonomous SRE investigation agent used during the 4-hour hands-on masterclass.

---

## 🧭 Repository Structure

```
.
├── index.html           # Workshop Portal & Master Syllabus
├── lab.html             # Interactive 8-Module Student Playbook
├── scoreboard.html      # Live Room Scoreboard & Leaderboard
├── calculator.html      # Token ROI, Latency & LLM Cost Modeler
├── guardrails.html      # Human-in-the-Loop Security Gate Visualizer
├── scorecard.html       # Individual Investigation Scorecard Formatter
├── projector.html       # Big-Screen High-Contrast Presenter View
├── agent/               # Autonomous SRE Investigation Agent
│   ├── config.yaml      # Declarative student levers (models, tools, scenarios)
│   ├── prompts.yaml     # Agent personas, security policies, reasoning modes
│   ├── mini_agent.py    # Main ReAct loop orchestrator & CLI commands
│   ├── tools.py         # 7 live cluster diagnostic tools + OpenAPI schemas
│   ├── config_loader.py # Zero-dep YAML parser & prompt assembler
│   ├── memory.py        # Episodic memory persistence (.json & .md ledgers)
│   ├── llm_client.py    # Zero-dep OpenAI-compatible HTTP client
│   ├── logger.py        # AuditLogger: turn-by-turn transcripts & traces
│   ├── mock_planner.py  # Deterministic offline simulation across 6 scenarios
│   └── README.md        # Dedicated agent documentation & guide
└── .gitignore           # Ignores runtime logs, traces, and Python bytecode
```

---

## 🌐 Workshop Web Suite

All web tools are zero-build, dependency-free static pages that can be opened directly in any modern browser:

| Web Tool | Description | Target User |
| :--- | :--- | :--- |
| **[index.html](index.html)** | **Workshop Portal** · Landing page, prerequisite checks, and quick links. | Attendees & Instructors |
| **[lab.html](lab.html)** | **Student Playbook** · Step-by-step instructions for all 8 hands-on modules. | Attendee Pairs |
| **[scoreboard.html](scoreboard.html)** | **Room Scoreboard** · Aggregates token burn, latency, cost, and ground-truth accuracy across pairs. | Room Display |
| **[calculator.html](calculator.html)** | **ROI & Cost Modeler** · Interactive token math, caching savings, and model price comparisons. | Attendees |
| **[guardrails.html](guardrails.html)** | **Security Guardrails** · Visualizes read-only enforcement and human confirmation gates. | Attendees |
| **[scorecard.html](scorecard.html)** | **Scorecard Formatter** · Formats investigation outputs into shareable scorecards. | Attendees |
| **[projector.html](projector.html)** | **Presenter Display** · High-contrast timer, agenda, and module progression view. | Facilitators |

### Running the Web Suite Locally
```bash
# Using Python standard library:
python3 -m http.server 8080

# Or using npx:
npx serve .
```
Then visit `http://localhost:8080` to access the workshop portal.

---

## 🤖 The Autonomous SRE Agent (`agent/`)

The agent is an autonomous, multi-turn ReAct investigation loop that connects to live Kubernetes clusters and the NudgeBee LLM Gateway. **No Python coding is required from attendees** — all experiments are driven via `config.yaml` and `prompts.yaml`.

### 🚀 Quickstart for Attendees

```bash
cd agent

# 1. Test live cluster diagnostic tools (Read-Only)
python3 mini_agent.py --test-tools

# 2. Run the autonomous investigation
python3 mini_agent.py

# 3. Inspect full transcript and execution traces
python3 mini_agent.py --logs      # Readable step-by-step transcript
python3 mini_agent.py --json      # Machine-parseable JSON trace
python3 mini_agent.py --prompt    # Inspect active prompt assembly & tool schemas
```

For full details on configuring models, toggling tools, and running scenarios, refer to the [Agent README](agent/README.md).

---

## 📚 The 8 Masterclass Modules

1. **Module 1: Agent Persona, Loop & Drift**  
   Understand ReAct loops, observation parsing, and preventing cognitive drift under complex incidents.
2. **Module 2: Small vs Frontier Models**  
   Compare frontier reasoning models (`gemini-3.8-flash`), open-weight models (`qwen3-235b`), and small edge models (`Nemotron-30B`) on cost, latency, and tool accuracy.
3. **Module 3: Tool Accuracy, Deprivation & Hallucinations**  
   Evaluate agent resilience when telemetry tools are removed. Red-team the agent with non-existent entities (`poisonedEntity`) to test grounding and abstention.
4. **Module 4: Context Windows & Prompt Caching**  
   Measure token burn across `structured_summary` (2k), `filtered_regex` (8k), and `raw_80k` context dumps. Quantify dollar savings from prompt caching.
5. **Module 5: Graph Blast Radius & Topology**  
   Use 1-hop microservice dependency graph lookups (`inspect_topology`) to trace cascading failures from storefront to downstream databases.
6. **Module 6: Tool Ceilings & Cognitive Modes**  
   Demonstrate how adding deployment history (`get_deploy_history`) breaks the diagnostic tool ceiling to pinpoint the exact bad commit (`a7f39b1`) under `badDeploy1405`.
7. **Module 7: Guardrails & Human-in-the-Loop**  
   Enforce strict read-only diagnostic boundaries. Require interactive operator authorization (`ask_human_approval`) before mutating commands (`kubectl rollout undo`).
8. **Module 8: Memory Tiers & Episodic Recall**  
   Test Turn 1 (`enable_memory: false`) first-principles investigation versus Turn 2 (`enable_memory: true`) episodic memory recall (`INC-4092`), assessing whether past remediations held.

---

## 🎯 Supported Incident Scenarios

| Scenario | Incident Type | Primary Evidence |
| :--- | :--- | :--- |
| `badDeploy1405` | Bad Config Deploy | `checkout` deployment revision 3 increased timeout 500ms $\to$ 5000ms. |
| `episodicRecurrence` | Memory Recurrence | PostgreSQL pool starvation matching `INC-4092` from 14 March. |
| `postgresFailure` | Crash / Unreachable | Database pool starvation and gRPC status 13 `INTERNAL`. |
| `emailMemoryLeak` | Resource Leak | Unbounded heap growth slope `deriv(...) > 0` and OOMKill Exit Code 137. |
| `postgresSlow` | Latency Cascade | Injected `pg_sleep` causing high p99 query duration without 5xx errors. |
| `poisonedEntity` | Red-Team Hallucination | Fictitious service alert; tests agent abstention and grounding. |

---

## ☁️ Deployment

### Cloudflare Pages
To deploy the static workshop web portal and tools to Cloudflare Pages:
```bash
npx wrangler pages deploy . --project-name nudgebee-workshop
```

---

## 📄 License
Internal training & educational materials. Developed for NudgeBee SRE Masterclasses.
