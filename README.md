# 🐝 Building AI Agents for SRE & AIOps

> **90-Minute Interactive Systems Engineering Masterclass**<br/>
> *8 Modules (M1–M8) + M0 Setup · 1 Running Scenario · Live Room Benchmarks · Autonomous SRE Agent*

Welcome to the **NudgeBee SRE & AIOps Agent Workshop** repository! This repository contains the complete curriculum, web-based operational tools, and an autonomous SRE investigation agent used during the 90-minute hands-on masterclass.

---

## 🧭 Repository Structure

```
.
├── index.html           # Workshop Portal & Master Syllabus
├── lab.html             # Interactive Hands-On Lab Playbook (M0–M8)
├── scoreboard.html      # Live Room Benchmarks & Metrics
├── calculator.html      # Token ROI, Latency & LLM Cost Modeler
├── guardrails.html      # Human-in-the-Loop Security Gate Visualizer
├── scorecard.html       # Buy vs Build Assessment Matrix
├── projector.html       # Big-Screen Buy vs Build 4-Quadrant Room Scatter
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
| **[lab.html](lab.html)** | **Student Playbook** · Step-by-step instructions for 8 hands-on modules (M1–M8) + M0 Setup. | Attendees & Teams |
| **[scoreboard.html](scoreboard.html)** | **Room Benchmarks** · Aggregates token burn, latency, cost, and ground-truth accuracy across teams. | Room Display |
| **[calculator.html](calculator.html)** | **ROI & Cost Modeler** · Interactive token math, caching savings, and model price comparisons. | Attendees |
| **[guardrails.html](guardrails.html)** | **Security Guardrails** · Visualizes read-only enforcement and human confirmation gates. | Attendees |
| **[scorecard.html](scorecard.html)** | **Buy vs Build Assessment** · Participant decision matrix evaluating AI capacity vs delivery runway. | Attendees |
| **[projector.html](projector.html)** | **Room Scatter Display** · Big-screen 4-quadrant room projection visualizer for Buy vs Build. | Facilitators |

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

#### Step 1: Launch Your Environment (Zero Setup)
Open this repository in **GitHub Codespaces** (Click **Code** → **Codespaces** → **Create codespace on main**).
Python 3.11, `kubectl`, port-forwarding, and VS Code extensions are pre-configured.

#### Step 2: Bootstrap Your Team Credentials
In the terminal, run the interactive bootstrap script:
```bash
./scripts/bootstrap-team.sh
```
Or run directly with your assigned team number and room passphrase:
```bash
./scripts/bootstrap-team.sh --team <YOUR_TEAM_NUM> --pass "<ROOM_PASSPHRASE>" --url "<STORAGE_URL>"
```
This automatically configures your team namespace, installs your scoped cluster `kubeconfig`, exports your LLM Gateway credentials, and tests cluster connectivity.

#### Step 3: Run the Agent & Verify Tools
```bash
cd agent

# 1. Test live cluster diagnostic tools (Read-Only)
python3 mini_agent.py --test-tools

# 2. Run your first autonomous investigation
python3 mini_agent.py --scenario badDeploy1405 --model mock

# 3. Inspect full transcript and execution traces
python3 mini_agent.py --logs      # Readable step-by-step transcript
python3 mini_agent.py --json      # Machine-parseable JSON trace
python3 mini_agent.py --prompt    # Inspect active prompt assembly & tool schemas
```

For full details on configuring models, toggling tools, and running scenarios, refer to the [Agent README](agent/README.md).

---

## 📚 The 9 Masterclass Modules (90 Min Total · 10m Each)

0. **Module 0: Environment Bootstrap & Zero-Setup Smoke Test**<br/>
   Configure team namespace, cluster access, verify `kubectl` credentials, and execute zero-setup diagnostic tool test.
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
   Test Turn 1 (`enable_memory_recall: false`, `persist_verified_resolution: true`) first-principles investigation baseline versus Turn 2 (`enable_memory_recall: true`) episodic memory recall (`INC-4092`), assessing whether past remediations held.

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

## ☁️ Static Web Suite Hosting

The workshop web suite is hosted at [https://nudgebee-workshop.pollux.in/](https://nudgebee-workshop.pollux.in/).
All tools are zero-build, static HTML/JS files that can be served directly from any static web server, object storage bucket (e.g. Google Cloud Storage, AWS S3), or container.

---

## 📄 License
Internal training & educational materials. Developed for NudgeBee SRE Masterclasses.
