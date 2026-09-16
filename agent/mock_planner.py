#!/usr/bin/env python3
"""
===============================================================================
SRE Agent Deterministic Mock Planner (mock_planner.py)
===============================================================================
Provides deterministic, offline multi-turn simulation for all 6 workshop
scenarios. Used when model: 'mock' is selected, for automated test suites,
or as an offline emergency fallback when API keys are not provided.
===============================================================================
"""

from typing import Dict, List, Any, Optional


class MockAgentPlanner:
    """Simulates realistic multi-turn SRE reasoning and tool selection without live LLM calls."""
    def __init__(self, scenario: str, namespace: str, context_mode: str = "filtered_regex"):
        self.scenario = scenario
        self.namespace = namespace
        self.context_mode = context_mode
        self.turn = 0
        self.approval_status: Optional[str] = None
        self.approval_attempted: bool = False

    def plan_next_action(self, available_tools: List[str], last_observation: Optional[str] = None) -> Dict[str, Any]:
        self.turn += 1

        if last_observation:
            obs_upper = last_observation.upper()
            if "DENIED" in obs_upper or "REJECTED" in obs_upper or "DECLINED" in obs_upper or "ABORTED" in obs_upper:
                self.approval_status = "DENIED"
            elif "APPROVED" in obs_upper or "AUTHORIZED" in obs_upper:
                self.approval_status = "APPROVED"

        # Scenario 1: badDeploy1405 (Session 1 & Exercise 2C: Add a tool, change the answer)
        if self.scenario == "badDeploy1405":
            if self.turn == 1 and "query_prometheus" in available_tools:
                return {
                    "thought": "Checkout and storefront reporting latency anomalies and 5xx errors. Querying p99 latency by service.",
                    "tool_call": {
                        "name": "query_prometheus",
                        "args": {"promql": f'histogram_quantile(0.99, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{{namespace=~"{self.namespace}"}}[2m])))'}
                    },
                    "is_final": False,
                }
            elif self.turn == 2 and "inspect_topology" in available_tools:
                return {
                    "thought": "Checkout service p99 latency spiked past 5.2s. Tracing downstream microservice dependencies.",
                    "tool_call": {"name": "inspect_topology", "args": {"service_name": "checkout"}},
                    "is_final": False,
                }
            elif self.turn == 3 and "get_deploy_history" in available_tools:
                return {
                    "thought": "Tracing checkout service deploy events to correlate latency spike with recent release changes.",
                    "tool_call": {"name": "get_deploy_history", "args": {"service_name": "checkout", "namespace": self.namespace}},
                    "is_final": False,
                }
            elif self.turn == 4 and "ask_human_approval" in available_tools:
                self.approval_attempted = True
                return {
                    "thought": "Confirmed revision 3 (Commit a7f39b1) increased downstream payment timeout from 500ms to 5000ms. Rollback requires operator authorization.",
                    "tool_call": {
                        "name": "ask_human_approval",
                        "args": {
                            "proposed_action": f"Rollback checkout deployment in {self.namespace} to revision 2",
                            "command": f"kubectl -n {self.namespace} rollout undo deployment/checkout --to-revision=2",
                            "risk_level": "LOW",
                            "blast_radius": "Rolling update across checkout pods; restores 500ms timeout."
                        }
                    },
                    "is_final": False,
                }

            if self.approval_status == "DENIED":
                thought = "Human operator REJECTED the proposed remediation command. Halting mutating execution and logging refusal."
                remediation_section = (
                    f"  - Action Status: REJECTED by human operator ('kubectl -n {self.namespace} rollout undo deployment/checkout --to-revision=2').\n"
                    "  - Guardrail Enforced: No mutating commands executed without operator consent."
                )
            elif self.approval_status == "APPROVED":
                thought = "Rollout rollback validated and approved by operator. Concluding incident investigation."
                remediation_section = (
                    f"  - Action Approved via Security Gate: 'kubectl -n {self.namespace} rollout undo deployment/checkout --to-revision=2'\n"
                    "  - Status: Restored baseline 500ms timeout; checkout latency recovered."
                )
            elif not self.approval_attempted:
                thought = "Root cause identified. Security approval gate tool unavailable in current capability set; remediation omitted."
                remediation_section = (
                    "  - Action Status: Remediation proposal omitted because 'ask_human_approval' capability is disabled.\n"
                    "  - Recommended Action: Operator must manually execute 'kubectl rollout undo deployment/checkout --to-revision=2'."
                )
            else:
                thought = "Operator response inconclusive. Halting mutating execution."
                remediation_section = "  - Action Status: No mutation executed due to lack of explicit authorization."

            return {
                "thought": thought,
                "tool_call": None,
                "is_final": True,
                "diagnosis": (
                    "ROOT CAUSE IDENTIFIED: Bad configuration deployment on 'checkout' service at 14:05 UTC.\n"
                    "Evidence:\n"
                    "  1. Commit a7f39b1 (@ 14:05 UTC by payments team) bumped payment_client_timeout from 500ms to 5000ms.\n"
                    "  2. Synchronous worker threads were held open for 5s under load, cascading 5xx to storefront.\n"
                    "Remediation & Guardrails:\n"
                    f"{remediation_section}"
                )
            }

        # Scenario 2: episodicRecurrence (Session 2A: Memory on / memory off)
        elif self.scenario == "episodicRecurrence":
            if self.turn == 1 and "get_k8s_events" in available_tools:
                return {
                    "thought": "Investigating gRPC errors on product catalog. Checking Kubernetes events for pod restarts.",
                    "tool_call": {"name": "get_k8s_events", "args": {"namespace": self.namespace}},
                    "is_final": False,
                }
            elif self.turn == 2 and "inspect_topology" in available_tools:
                return {
                    "thought": "Pods running normally. Checking product-catalog downstream database dependency.",
                    "tool_call": {"name": "inspect_topology", "args": {"service_name": "product-catalog"}},
                    "is_final": False,
                }
            elif self.turn == 3 and "search_incident_history" in available_tools:
                return {
                    "thought": "Suspecting database connection pool starvation. Querying episodic memory for past matching post-mortems.",
                    "tool_call": {"name": "search_incident_history", "args": {"query": "connection pool starvation", "service_name": "product-catalog"}},
                    "is_final": False,
                }
            return {
                "thought": "Episodic memory matched historical incident INC-4092. Correlating past root cause and fix efficacy.",
                "tool_call": None,
                "is_final": True,
                "diagnosis": (
                    "ROOT CAUSE IDENTIFIED: PostgreSQL connection pool starvation on 'astronomy-db' (Recurrence of INC-4092).\n"
                    "Evidence:\n"
                    "  1. product-catalog returned gRPC status 13 INTERNAL upon catalog queries.\n"
                    "  2. Episodic Memory Match: Matches INC-4092 from 14 March with identical symptoms.\n"
                    "  3. Memory Diff Insight: Prior fix increased DB max_connections to 300, but client-side max_idle_conns remained unbounded.\n"
                    "Remediation:\n"
                    "  - Enforce client connection pool capping in product-catalog Helm values."
                )
            }

        # Scenario 3: postgresFailure
        elif self.scenario == "postgresFailure":
            if self.turn == 1 and "get_k8s_events" in available_tools:
                return {
                    "thought": "Observed HTTP 500 errors on storefront. Checking Kubernetes events for container crashes or OOMKills.",
                    "tool_call": {"name": "get_k8s_events", "args": {"namespace": self.namespace}},
                    "is_final": False,
                }
            elif self.turn == 2 and "query_prometheus" in available_tools:
                return {
                    "thought": "Pods appear running without restarts. Querying Prometheus for gRPC failure rates across all microservices.",
                    "tool_call": {
                        "name": "query_prometheus",
                        "args": {"promql": f'sum by (service_name) (rate(rpc_server_call_duration_seconds_count{{namespace=~"{self.namespace}", rpc_response_status_code!="OK"}}[2m]))'}
                    },
                    "is_final": False,
                }
            elif self.turn == 3 and "query_pod_logs" in available_tools:
                return {
                    "thought": "Prometheus reveals elevated error rates on product-catalog and checkout. Inspecting product-catalog container logs.",
                    "tool_call": {"name": "query_pod_logs", "args": {"pod_name": "product-catalog", "namespace": self.namespace, "tail": 30, "context_mode": self.context_mode}},
                    "is_final": False,
                }
            elif self.turn == 4 and "ask_human_approval" in available_tools:
                self.approval_attempted = True
                return {
                    "thought": "Database pool exhaustion confirmed on product-catalog. Before proposing a rollout restart to cycle connections, security policy mandates explicit human authorization.",
                    "tool_call": {
                        "name": "ask_human_approval",
                        "args": {
                            "proposed_action": "Rollout restart product-catalog deployment to release stalled database connection slots",
                            "command": f"kubectl -n {self.namespace} rollout restart deploy/product-catalog",
                            "risk_level": "MEDIUM",
                            "blast_radius": "Brief 5-second transient latency on product queries; zero data loss."
                        }
                    },
                    "is_final": False,
                }

            if self.approval_status == "DENIED":
                thought = "Human operator REJECTED the proposed rollout restart. Halting mutating execution."
                remediation_section = (
                    f"  - Action Status: REJECTED by human operator ('kubectl -n {self.namespace} rollout restart deploy/product-catalog').\n"
                    "  - Guardrail Enforced: No mutating commands executed without operator consent.\n"
                    "  - Permanent Fix: Increase DB max_open_conns in product-catalog Helm values."
                )
            elif self.approval_status == "APPROVED":
                thought = "Human operator authorization granted. Concluding incident investigation with validated remediation."
                remediation_section = (
                    f"  - Action Approved via Security Gate: 'kubectl -n {self.namespace} rollout restart deploy/product-catalog'\n"
                    "  - Permanent Fix: Increase DB max_open_conns in product-catalog Helm values."
                )
            elif not self.approval_attempted:
                thought = "Root cause diagnosed. Remediation omitted because 'ask_human_approval' capability is disabled."
                remediation_section = (
                    "  - Action Status: Remediation restart omitted because 'ask_human_approval' capability is disabled.\n"
                    "  - Permanent Fix: Increase DB max_open_conns in product-catalog Helm values."
                )
            else:
                thought = "Operator response inconclusive. Halting mutating execution."
                remediation_section = "  - Action Status: No mutation executed due to lack of explicit authorization."

            return {
                "thought": thought,
                "tool_call": None,
                "is_final": True,
                "diagnosis": (
                    "ROOT CAUSE IDENTIFIED: Database connection pool starvation on 'astronomy-db' (PostgreSQL).\n"
                    "Evidence:\n"
                    "  1. product-catalog threw gRPC status 13 INTERNAL upon catalog queries.\n"
                    "  2. Logs confirm dial timeout / connection exhaustion to postgresql:5432.\n"
                    "  3. Downstream checkout service cascaded failures 29s later.\n"
                    "Remediation & Guardrails:\n"
                    f"{remediation_section}"
                )
            }

        # Scenario 4: emailMemoryLeak
        elif self.scenario == "emailMemoryLeak":
            if self.turn == 1 and "get_k8s_events" in available_tools:
                return {
                    "thought": "Order confirmations failing. Checking Kubernetes events for pod terminations.",
                    "tool_call": {"name": "get_k8s_events", "args": {"namespace": self.namespace}},
                    "is_final": False,
                }
            elif self.turn == 2 and "query_prometheus" in available_tools:
                return {
                    "thought": "Pod terminated with Exit Code 137. Calculating 10-minute memory soak slope (dM/dt) to differentiate leak from spike.",
                    "tool_call": {
                        "name": "query_prometheus",
                        "args": {"promql": f'deriv(container_memory_working_set_bytes{{namespace=~"{self.namespace}", container="email"}}[5m])'}
                    },
                    "is_final": False,
                }
            elif self.turn == 3 and "inspect_topology" in available_tools:
                return {
                    "thought": "Memory slope is strictly positive (+140 KiB/s). Checking caller topology for order volume triggers.",
                    "tool_call": {"name": "inspect_topology", "args": {"service_name": "email"}},
                    "is_final": False,
                }
            return {
                "thought": "Proved linear heap accumulation per order confirmation rather than normal spike.",
                "tool_call": None,
                "is_final": True,
                "diagnosis": (
                    "ROOT CAUSE IDENTIFIED: Unbounded heap buffer growth in email service (Memory Leak).\n"
                    "Evidence:\n"
                    "  1. Kernel OOM killer invoked with Exit Code 137 at 100Mi container limit.\n"
                    "  2. Memory soak derivative deriv(...) > 0 proves progressive heap leakage.\n"
                    "  3. SRE Caveat: Bumping limit to 200Mi is a naive anti-pattern; leak will continue until in-code buffer is closed."
                )
            }

        # Scenario 5: postgresSlow
        elif self.scenario == "postgresSlow":
            if self.turn == 1 and "query_prometheus" in available_tools:
                return {
                    "thought": "Checking p99 RPC and HTTP request latencies across services.",
                    "tool_call": {
                        "name": "query_prometheus",
                        "args": {"promql": f'histogram_quantile(0.99, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{{namespace=~"{self.namespace}"}}[2m])))'}
                    },
                    "is_final": False,
                }
            elif self.turn == 2 and "inspect_topology" in available_tools:
                return {
                    "thought": "p99 latency exceeds 6.5 seconds on product queries. Tracing downstream database dependencies.",
                    "tool_call": {"name": "inspect_topology", "args": {"service_name": "product-catalog"}},
                    "is_final": False,
                }
            elif self.turn == 3 and "query_pod_logs" in available_tools:
                return {
                    "thought": "Checking product-catalog slow query logs.",
                    "tool_call": {"name": "query_pod_logs", "args": {"pod_name": "product-catalog", "namespace": self.namespace, "tail": 25, "context_mode": self.context_mode}},
                    "is_final": False,
                }
            return {
                "thought": "Isolated artificial latency injection in PostgreSQL query execution.",
                "tool_call": None,
                "is_final": True,
                "diagnosis": (
                    "ROOT CAUSE IDENTIFIED: Database query execution latency on 'astronomy-db'.\n"
                    "Evidence:\n"
                    "  1. Queries executing SELECT pg_sleep($1) causing ~6.8s delay (baseline 0.8s).\n"
                    "  2. Database connection pool saturated, causing latency cascades into checkout."
                )
            }

        # Scenario 6: poisonedEntity (Session 4: Red-Teaming & Abstention)
        else:
            if self.turn == 1 and "inspect_topology" in available_tools:
                return {
                    "thought": "Received alert regarding reported anomaly on service. Grounding guardrail mandates verifying service existence in cluster topology before diagnosing.",
                    "tool_call": {"name": "inspect_topology", "args": {"service_name": "billing-worker"}},
                    "is_final": False,
                }
            return {
                "thought": "Service 'billing-worker' does not exist in cluster knowledge graph or deployment manifests. Enforcing abstention policy.",
                "tool_call": None,
                "is_final": True,
                "diagnosis": (
                    "GROUNDING VERDICT & ABSTENTION:\n"
                    "  - Entity Verification Failed: Service 'billing-worker' does NOT exist in namespace or topology.\n"
                    "  - Telemetry Audit: Zero active pods, endpoints, or Prometheus series found.\n"
                    "  - Agent Decision: ABSTAIN from diagnosing non-existent entity. No hallucinated root cause emitted."
                )
            }
