#!/usr/bin/env python3
"""
===============================================================================
Building AI Agents for SRE & AIOps · Cluster Tools Library
===============================================================================
Provides live telemetry tools for Kubernetes, Prometheus, and microservice
topology inspection, plus human-in-the-loop security approval gates.
===============================================================================
"""

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Dict, Any, List, Tuple

# How far back pod logs are read. Older lines are almost always residue from a
# previous scenario run rather than evidence about the incident under investigation.
LOG_WINDOW = os.getenv("AGENT_LOG_WINDOW", "10m")

# RFC 1123 DNS label regex for safe Kubernetes resource names
K8S_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def validate_k8s_name(name: str, field_name: str = "parameter") -> str:
    """Validate resource names against Kubernetes RFC 1123 rules to prevent command injection."""
    clean = str(name).strip()
    if not clean or len(clean) > 63 or not K8S_NAME_RE.match(clean):
        raise ValueError(
            f"Invalid {field_name} '{name}': must be 1-63 chars, lowercase alphanumeric or dashes (RFC 1123)."
        )
    return clean


def run_cmd(args: List[str], timeout: int = 10) -> Tuple[int, str, str]:
    """
    Safely execute external commands without shell interpolation (shell=False).
    Returns (returncode, stdout, stderr).
    """
    try:
        res = subprocess.run(args, shell=False, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout} seconds."
    except FileNotFoundError:
        return -1, "", f"Executable '{args[0]}' not found on system PATH."
    except Exception as e:
        return -1, "", f"Execution error: {e}"


def get_k8s_events(namespace: str) -> str:
    """Inspect recent Kubernetes events for pod crashes, OOMKills, or scheduling errors."""
    try:
        ns = validate_k8s_name(namespace, "namespace")
    except ValueError as err:
        return f"Tool Input Validation Error: {err}"

    # NOTE: `kubectl get` has no --tail flag (that belongs to `kubectl logs`),
    # so we fetch the sorted event list and keep the newest rows in Python.
    cmd = [
        "kubectl", "-n", ns, "get", "events",
        "--sort-by=.metadata.creationTimestamp",
        "-o", "custom-columns=TIME:.metadata.creationTimestamp,TYPE:.type,REASON:.reason,MESSAGE:.message",
    ]
    code, stdout, stderr = run_cmd(cmd)
    if code != 0:
        return f"Kubernetes API error querying events in namespace '{ns}' (exit {code}): {stderr or stdout}"
    if not stdout or "No resources found" in stdout:
        return f"No abnormal Kubernetes events recorded in namespace '{ns}'."

    lines = stdout.splitlines()
    header, rows = lines[0], lines[1:]
    if not rows:
        return f"No abnormal Kubernetes events recorded in namespace '{ns}'."
    return "\n".join([header] + rows[-15:])


def query_prometheus(promql: str, prom_url: str = "http://localhost:9090", k8s_proxy_ns: str = "nudgebee-agent") -> str:
    """
    Execute an instant PromQL query against Prometheus.
    Tries local HTTP endpoint first; if unavailable, seamlessly falls back to
    the Kubernetes API server proxy (kubectl get --raw) so zero port-forwarding is needed.
    """
    base_url = prom_url.rstrip("/")
    params = urllib.parse.urlencode({"query": promql})
    url = f"{base_url}/api/v1/query?{params}"
    data = None
    proxy_err = ""

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SRE-MiniAgent/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        # Fallback via Kubernetes API server proxy
        try:
            safe_proxy_ns = validate_k8s_name(k8s_proxy_ns, "proxy_namespace")
        except ValueError:
            safe_proxy_ns = "nudgebee-agent"

        k8s_proxy_path = (
            f"/api/v1/namespaces/{safe_proxy_ns}/services/"
            f"nudgebee-prometheus-kube-p-prometheus:9090/proxy/api/v1/query?{params}"
        )
        code, stdout, stderr = run_cmd(["kubectl", "get", "--raw", k8s_proxy_path], timeout=10)
        if code == 0 and stdout and stdout.startswith("{"):
            try:
                data = json.loads(stdout)
            except Exception as e:
                proxy_err = f"JSON decode error: {e}"
        else:
            proxy_err = stderr or stdout or "Proxy endpoint returned empty response."

    if not data or data.get("status") != "success":
        detail = f" ({proxy_err})" if proxy_err else ""
        return f"Prometheus query failed or returned invalid response for '{promql}'{detail}"

    results = data.get("data", {}).get("result", [])
    if not results:
        return "Prometheus query returned 0 active series."

    simplified = []
    for r in results[:6]:
        metric = r.get("metric", {})
        val = r.get("value", [None, None])[1]
        svc = metric.get("service_name") or metric.get("app") or metric.get("pod") or metric.get("job") or "target"
        try:
            f_val = float(val)
            val_str = f"{f_val:.4f}" if f_val < 1.0 else f"{f_val:.2f}"
        except (ValueError, TypeError):
            val_str = str(val)
        simplified.append(f"{svc} -> {val_str}")

    return " | ".join(simplified)


def query_pod_logs(pod_name_prefix: str, namespace: str, tail: int = 30, context_mode: str = "filtered_regex") -> str:
    """Fetch and filter latest stdout logs from a target microservice pod."""
    try:
        ns = validate_k8s_name(namespace, "namespace")
        prefix = validate_k8s_name(pod_name_prefix, "pod_name_prefix")
    except ValueError as err:
        return f"Tool Input Validation Error: {err}"

    safe_tail = max(1, min(int(tail), 200))

    # Safe pod discovery via kubectl jsonpath
    find_cmd = ["kubectl", "-n", ns, "get", "pods", "-o", "jsonpath={.items[*].metadata.name}"]
    code, stdout, stderr = run_cmd(find_cmd)
    if code != 0:
        return f"Kubernetes API error listing pods in namespace '{ns}' (exit {code}): {stderr or stdout}"

    all_pods = stdout.split()
    matched = [p for p in all_pods if prefix in p]
    if not matched:
        return f"No pods matching prefix '{prefix}' found in namespace '{ns}'. Active pods: {', '.join(all_pods) if all_pods else 'none'}."

    target_pod = matched[0]
    # Bound the window as well as the line count. Each scenario run injects a fault and
    # clears it again, so error lines from a previous run linger in the ring buffer and
    # would otherwise be read as live symptoms - the agent has diagnosed an already-
    # cleared fault this way. LOG_WINDOW keeps observations close to the current state.
    log_cmd = [
        "kubectl", "-n", ns, "logs", target_pod,
        f"--tail={safe_tail}", f"--since={LOG_WINDOW}",
    ]
    code, stdout, stderr = run_cmd(log_cmd)
    if code != 0:
        return f"Error retrieving logs for pod '{target_pod}' in namespace '{ns}' (exit {code}): {stderr or stdout}"
    if not stdout:
        return f"No stdout/stderr lines returned for pod '{target_pod}' in namespace '{ns}'."

    lines = stdout.splitlines()
    if context_mode == "filtered_regex":
        error_keywords = ["error", "fatal", "panic", "fail", "timeout", "refused", "closed", "exhausted", "starvation", "oom"]
        error_lines = [l for l in lines if any(k in l.lower() for k in error_keywords)]
        if error_lines:
            return "\n".join(error_lines[-12:])
        return "Log scan complete: No obvious error keywords found in the last lines."
    elif context_mode == "structured_summary":
        error_count = sum(1 for l in lines if "error" in l.lower())
        sample = lines[-1] if lines else "none"
        return f"Structured Summary ({target_pod}): {len(lines)} lines scanned. Found {error_count} error events. Last line: '{sample}'"

    return "\n".join(lines[-safe_tail:])


def inspect_topology(service_name: str) -> str:
    """1-Hop dependency lookup from the microservices knowledge graph (Module 5)."""
    TOPOLOGY = {
        "frontend": {"callers": ["client"], "dependencies": ["checkout", "product-catalog", "cart"]},
        "checkout": {"callers": ["frontend"], "dependencies": ["product-catalog", "payment", "email", "shipping"]},
        "product-catalog": {"callers": ["frontend", "checkout"], "dependencies": ["astronomy-db (PostgreSQL)"]},
        "email": {"callers": ["checkout"], "dependencies": []},
        "payment": {"callers": ["checkout"], "dependencies": []},
        "shipping": {"callers": ["checkout"], "dependencies": []},
        "cart": {"callers": ["frontend"], "dependencies": ["valkey-cart (Redis)"]},
        "astronomy-db": {"callers": ["product-catalog"], "dependencies": []},
    }
    try:
        svc = validate_k8s_name(service_name, "service_name")
    except ValueError as err:
        return f"Tool Input Validation Error: {err}"

    data = TOPOLOGY.get(svc)
    if not data:
        return f"Service '{svc}' not found in knowledge graph topology."
    return f"Service '{svc}' -> Upstream Callers: {data['callers']} | Downstream Dependencies: {data['dependencies']}"


def get_deploy_history(service_name: str, namespace: str, is_mock: bool = False) -> str:
    """
    Inspect deployment rollout history and revision causes (Session 1 & 2C).
    In live mode, queries Kubernetes directly without fabricating synthetic data.
    """
    try:
        ns = validate_k8s_name(namespace, "namespace")
        svc = validate_k8s_name(service_name, "service_name")
    except ValueError as err:
        return f"Tool Input Validation Error: {err}"

    cmd = ["kubectl", "-n", ns, "rollout", "history", f"deployment/{svc}"]
    code, stdout, stderr = run_cmd(cmd)

    if code == 0 and stdout:
        return f"Deployment '{svc}' Rollout History (Namespace: {ns}):\n{stdout}"

    # In mock simulation mode, return curated historical scenario diff
    if is_mock:
        if "checkout" in svc:
            return (
                f"Deployment '{svc}' Rollout History (Namespace: {ns}):\n"
                f"REVISION  DEPLOY-TIME     AUTHOR                        COMMIT / CHANGE-CAUSE\n"
                f"1         08:00 UTC       ci-runner@company.internal    Initial release (Helm chart v0.41.1)\n"
                f"2         11:30 UTC       infra-team@company.internal   Bump base container image to alpine:3.19\n"
                f"3         14:05 UTC       payments@company.internal     Commit a7f39b1: 'perf: tune client timeouts for downstream payment service'\n"
                f"          [CONFIG DIFF IN REVISION 3]:\n"
                f"          - payment_client_timeout: 500ms\n"
                f"          + payment_client_timeout: 5000ms\n"
                f"          [STATUS]: 1/1 replicas updated at 14:05:22 UTC. Rollout complete."
            )
        return (
            f"Deployment '{svc}' Rollout History (Namespace: {ns}):\n"
            f"REVISION  CHANGE-CAUSE\n"
            f"1         Initial release (Helm chart v0.41.1)\n"
            f"2         Stable baseline rollout (3 days ago)\n"
            f"Status: Current revision is stable. No deploys in last 24h."
        )

    # In live mode, report the truthful Kubernetes error
    return f"Kubernetes API error inspecting rollout history for deployment/{svc} in namespace '{ns}' (exit {code}): {stderr or stdout}"


def search_incident_history(query: str, service_name: str = "", enable_memory: bool = True) -> str:
    """
    Episodic Memory Store (Session 2A: 'Memory on / memory off').
    Loads persistent post-mortems from memory/episodic_memory.json to recall prior incident resolutions.
    When enable_memory is False, strictly denies retrieval to enable clean A/B comparison.
    """
    if not enable_memory:
        return "Episodic memory is DISABLED (enable_memory_recall: false). Historical context cannot be retrieved."

    mem_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "episodic_memory.json")
    incident_db = []
    if os.path.exists(mem_file):
        try:
            with open(mem_file, "r", encoding="utf-8") as f:
                incident_db = json.load(f)
        except Exception:
            pass

    if not incident_db:
        # Fallback default catalog if file is empty
        incident_db = [
            {
                "incident_id": "INC-4092",
                "date": "14 March, 09:30 UTC",
                "title": "Database connection pool starvation on astronomy-db",
                "service": "product-catalog",
                "keywords": ["database", "postgres", "pool", "starvation", "connection", "astronomy-db", "max_connections"],
                "symptoms": "product-catalog returned gRPC 13 INTERNAL; storefront HTTP 500 errors spiked.",
                "root_cause": "Traffic spike caused product-catalog to scale from 2 to 8 pods. Each pod opened 20 PostgreSQL connections, exceeding PostgreSQL max_connections=100.",
                "fix": "Increased PostgreSQL max_connections to 300, set PgBouncer pool_mode=transaction, and set product-catalog HPA minReplicas=4.",
                "did_fix_hold": "Partially held. Recurrence expected if client-side max_idle_conns is unbounded."
            },
            {
                "incident_id": "INC-3881",
                "date": "28 February, 16:15 UTC",
                "title": "Valkey / Redis session eviction storm",
                "service": "cart",
                "keywords": ["cart", "valkey", "redis", "memory", "eviction", "cache"],
                "symptoms": "User shopping carts emptied unexpectedly; Redis CPU hit 100%.",
                "root_cause": "No TTL set on guest session cart keys, causing memory exhaustion and allkeys-lru eviction.",
                "fix": "Configured maxmemory-policy volatile-lru and enforced 24h TTL on cart items.",
                "did_fix_hold": "Yes, zero cart evictions observed since fix."
            }
        ]

    q = str(query).lower().strip()
    s = str(service_name).lower().strip()
    matched = []

    for inc in incident_db:
        score = 0
        inc_svc = str(inc.get("service", "")).lower()
        inc_title = str(inc.get("title", "")).lower()
        inc_symptoms = str(inc.get("symptoms", "")).lower()
        inc_root = str(inc.get("root_cause", "")).lower()

        if s and s in inc_svc:
            score += 3
        for kw in inc.get("keywords", []):
            if kw.lower() in q:
                score += 2
        for word in q.split():
            if len(word) > 3:
                if word in inc_title:
                    score += 2
                if word in inc_symptoms or word in inc_root:
                    score += 1

        if score > 0:
            matched.append((score, inc))

    matched.sort(key=lambda x: x[0], reverse=True)

    if not matched:
        return f"No historical post-mortems found in episodic memory matching query '{query}'."

    top_inc = matched[0][1]
    return (
        f"📋 [EPISODIC MEMORY MATCH: {top_inc.get('incident_id')} ({top_inc.get('date', 'Prior Run')})]\n"
        f"├── Incident Title : {top_inc.get('title')}\n"
        f"├── Service Impact : {top_inc.get('service')}\n"
        f"├── Symptoms       : {top_inc.get('symptoms')}\n"
        f"├── Root Cause     : {top_inc.get('root_cause')}\n"
        f"├── Prior Fix      : {top_inc.get('fix') or top_inc.get('remediation')}\n"
        f"└── Did Fix Hold?  : {top_inc.get('did_fix_hold', 'Unknown')}"
    )


def ask_human_approval(proposed_action: str, command: str, risk_level: str = "MEDIUM", blast_radius: str = "Unknown") -> str:
    """
    Security Guardrail Tool (Module 7).
    Invoked when the agent determines a mutating or destructive remediation is needed.
    Presents the proposed change to the human operator for explicit authorization.
    Never auto-approves by default.
    """
    card = [
        f"\n🛡️  [SECURITY CONFIRMATION GATE TRIGGERED]",
        f"├── Proposed Action : {proposed_action}",
        f"├── Risk Level      : {risk_level.upper()}",
        f"├── Command         : {command}",
        f"└── Blast Radius    : {blast_radius}",
    ]
    card_text = "\n".join(card)
    print(card_text)

    # Check for automated test override
    auto_approve = os.getenv("AUTO_APPROVE_HUMAN_GATE", "").strip().lower() in ("true", "1", "yes")

    if auto_approve:
        decision = "APPROVED"
        reason = "Automated test environment authorized execution (AUTO_APPROVE_HUMAN_GATE=true)."
    elif sys.stdin.isatty():
        try:
            print("\n⚠️  MUTATING ACTION PROPOSED BY AI AGENT.")
            user_input = input("👉 Type 'approve' to grant execution, or press Enter to reject: ").strip().lower()
            if user_input in ("approve", "yes", "y"):
                decision = "APPROVED"
                reason = "Explicitly authorized by human operator in terminal."
            else:
                decision = "DENIED"
                reason = "Rejected by human operator."
        except (EOFError, KeyboardInterrupt):
            decision = "DENIED"
            reason = "Human approval prompt cancelled."
    else:
        decision = "DENIED"
        reason = "Non-interactive headless terminal: Mutating action DENIED at security gate (set AUTO_APPROVE_HUMAN_GATE=true for automated tests)."

    return (
        f"{card_text}\n"
        f"👉 OPERATOR DECISION: [{decision}] - {reason}"
    )


# ==============================================================================
# STANDARD OPENAI FUNCTION-CALLING TOOL SCHEMAS
# ==============================================================================
OPENAI_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_k8s_events",
            "description": "READ-ONLY: Inspect recent Kubernetes events for pod crashes, OOMKills, or scheduling errors in the specified namespace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "The Kubernetes namespace to inspect, e.g. 'group-1'."
                    }
                },
                "required": ["namespace"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_prometheus",
            "description": "READ-ONLY: Execute instant PromQL query against Prometheus to retrieve error rates, latencies, or container memory saturation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "promql": {
                        "type": "string",
                        "description": "The PromQL query expression, e.g. 'sum by (service_name) (rate(rpc_server_call_duration_seconds_count[2m]))'."
                    }
                },
                "required": ["promql"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_pod_logs",
            "description": "READ-ONLY: Fetch and filter recent stdout/stderr logs from a specific microservice container in the target namespace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {
                        "type": "string",
                        "description": "Name or prefix of the target pod/service, e.g. 'product-catalog', 'checkout', 'email'."
                    },
                    "namespace": {
                        "type": "string",
                        "description": "The Kubernetes namespace containing the pod."
                    },
                    "tail": {
                        "type": "integer",
                        "description": "Number of recent lines to retrieve (default: 30)."
                    }
                },
                "required": ["pod_name", "namespace"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_topology",
            "description": "READ-ONLY: Lookup 1-hop upstream callers and downstream dependencies of a microservice from the architecture knowledge graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the microservice, e.g. 'frontend', 'product-catalog', 'checkout', 'email'."
                    }
                },
                "required": ["service_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_human_approval",
            "description": "SECURITY GATE: Request human operator authorization before performing any potentially mutating, destructive, or state-changing remediation action (e.g. pod rollout restart, scaling, configuration rollback). You MUST specify the proposed command, risk level, and estimated blast radius.",
            "parameters": {
                "type": "object",
                "properties": {
                    "proposed_action": {
                        "type": "string",
                        "description": "Human-readable description of what action needs to be taken (e.g. 'Restart product-catalog pods to cycle connection pool')."
                    },
                    "command": {
                        "type": "string",
                        "description": "The exact shell or kubectl command to be executed."
                    },
                    "risk_level": {
                        "type": "string",
                        "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                        "description": "Estimated operational risk level."
                    },
                    "blast_radius": {
                        "type": "string",
                        "description": "Estimated customer impact and dependent services affected during execution."
                    }
                },
                "required": ["proposed_action", "command", "risk_level", "blast_radius"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_deploy_history",
            "description": "READ-ONLY: Inspect recent deployment rollout history, commit hashes, config diffs, and deploy timestamps for a service in Kubernetes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "The service or deployment name, e.g. 'checkout', 'product-catalog', 'frontend'."
                    },
                    "namespace": {
                        "type": "string",
                        "description": "The Kubernetes namespace, e.g. 'group-1'."
                    }
                },
                "required": ["service_name", "namespace"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_incident_history",
            "description": "READ-ONLY: Search episodic memory and historical post-mortems to discover if this incident or symptom has occurred before and what fixed it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or symptoms, e.g. 'connection pool exhaustion', 'cart empty', 'checkout timeout'."
                    },
                    "service_name": {
                        "type": "string",
                        "description": "Optional service name to narrow the episodic search."
                    }
                },
                "required": ["query"]
            }
        }
    }
]

TOOL_DISPATCH: Dict[str, Any] = {
    "get_k8s_events": lambda args: get_k8s_events(args.get("namespace", "default")),
    "query_prometheus": lambda args: query_prometheus(args.get("promql", "")),
    "query_pod_logs": lambda args: query_pod_logs(
        args.get("pod_name", ""),
        args.get("namespace", "default"),
        int(args.get("tail", 30)),
        args.get("context_mode", "filtered_regex")
    ),
    "inspect_topology": lambda args: inspect_topology(args.get("service_name", "")),
    "ask_human_approval": lambda args: ask_human_approval(
        args.get("proposed_action", "Remediation action"),
        args.get("command", "kubectl ..."),
        args.get("risk_level", "MEDIUM"),
        args.get("blast_radius", "Local service impact")
    ),
    "get_deploy_history": lambda args: get_deploy_history(
        args.get("service_name", ""),
        args.get("namespace", "default"),
        is_mock=bool(args.get("is_mock", False))
    ),
    "search_incident_history": lambda args: search_incident_history(
        args.get("query", ""),
        args.get("service_name", ""),
        enable_memory=bool(args.get("enable_memory", False))
    ),
}


def debug_enabled(explicit: bool = False) -> bool:
    """Debug mode: set AGENT_DEBUG=1 (or pass --debug) to disable all output capping."""
    return explicit or os.getenv("AGENT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _summarize(output: str, passed: bool, limit: int = 120, debug: bool = False) -> str:
    """Keep successful output short, but never truncate a failure's error detail.

    In debug mode nothing is capped at all - the raw tool output is returned verbatim.
    """
    if debug_enabled(debug) or not passed:
        return output
    return output if len(output) <= limit else output[:limit] + "..."


def is_tool_success(output: str) -> bool:
    """Evaluates whether a tool execution output represents a successful operation."""
    if not output:
        return False
    error_prefixes = (
        "error:",
        "kubernetes api error",
        "prometheus query failed",
        "tool input validation error",
        "command timed out",
        "executable '",
        "execution error:",
    )
    lower = output.strip().lower()
    return not any(lower.startswith(prefix) for prefix in error_prefixes)


def test_all_tools(namespace: str = "group-1", debug: bool = False) -> List[Tuple[str, bool, str]]:
    """Runs a live health check on all tools against the cluster.

    With debug=True (or AGENT_DEBUG=1) every tool's raw output is returned uncapped.
    """
    debug = debug_enabled(debug)
    results = []
    
    # 1. Test get_k8s_events
    try:
        res = get_k8s_events(namespace)
        passed = is_tool_success(res)
        results.append(("get_k8s_events", passed, _summarize(res, passed, debug=debug)))
    except Exception as e:
        results.append(("get_k8s_events", False, f"{type(e).__name__}: {e}"))

    # 2. Test query_prometheus
    try:
        res = query_prometheus("up")
        passed = is_tool_success(res)
        results.append(("query_prometheus", passed, _summarize(res, passed, debug=debug)))
    except Exception as e:
        results.append(("query_prometheus", False, f"{type(e).__name__}: {e}"))

    # 3. Test query_pod_logs
    try:
        res = query_pod_logs("product-catalog", namespace, tail=10)
        passed = is_tool_success(res)
        results.append(("query_pod_logs", passed, _summarize(res, passed, debug=debug)))
    except Exception as e:
        results.append(("query_pod_logs", False, f"{type(e).__name__}: {e}"))

    # 4. Test inspect_topology
    try:
        res = inspect_topology("product-catalog")
        passed = is_tool_success(res) and ("Dependencies" in res or "astronomy-db" in res)
        results.append(("inspect_topology", passed, _summarize(res, passed, debug=debug)))
    except Exception as e:
        results.append(("inspect_topology", False, f"{type(e).__name__}: {e}"))

    # 5. Test ask_human_approval
    try:
        res = ask_human_approval("Test pod restart", "kubectl rollout restart deploy/product-catalog", "LOW", "Zero downtime rolling restart")
        passed = "SECURITY GATE" in res or "APPROVED" in res or "DENIED" in res
        gate_line = res.strip().splitlines()[-1] if res.strip() else "Gate online"
        results.append(("ask_human_approval", passed, res if (not passed or debug) else _summarize(gate_line, passed)))
    except Exception as e:
        results.append(("ask_human_approval", False, f"{type(e).__name__}: {e}"))

    # 6. Test get_deploy_history
    try:
        res = get_deploy_history("checkout", namespace, is_mock=False)
        passed = is_tool_success(res) and "Rollout History" in res
        results.append(("get_deploy_history", passed, res if (not passed or debug) else res.splitlines()[0]))
    except Exception as e:
        results.append(("get_deploy_history", False, f"{type(e).__name__}: {e}"))

    # 7. Test search_incident_history
    try:
        res = search_incident_history("connection pool", "product-catalog", enable_memory=True)
        passed = is_tool_success(res) and "INC-4092" in res
        results.append(("search_incident_history", passed, res if (not passed or debug) else "Episodic memory matched INC-4092"))
    except Exception as e:
        results.append(("search_incident_history", False, f"{type(e).__name__}: {e}"))

    return results
