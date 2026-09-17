#!/usr/bin/env python3
"""
===============================================================================
Workshop Scenario Fault Injection (fault_injection.py)
===============================================================================
Turns a workshop scenario into a REAL fault in the team's namespace.

The agent's --scenario flag only changes its prompt and its grading criteria; it
does not break anything. Without injection the agent investigates a healthy
cluster, finds nothing, and is graded as failing.

This runs in the CLI harness BEFORE the investigation loop starts. It is never
exposed as an agent tool: the agent's own toolset stays strictly read-only so the
workshop's security-guardrails module remains honest. The harness sets the stage;
the agent then walks on and can only observe.
===============================================================================
"""

import json
import time
from typing import Dict, Optional, Tuple

from tools import run_cmd, validate_k8s_name

CONFIGMAP = "flagd-config"
CM_KEY = "demo.flagd.json"

# Scenario -> (flagd flag, variant). Scenarios mapped to None need no cluster
# fault: they are driven by rollout history, episodic memory, or a deliberately
# non-existent service name.
SCENARIO_FAULTS: Dict[str, Optional[Tuple[str, str]]] = {
    "postgresFailure": ("postgresFailure", "on"),
    "postgresSlow": ("postgresSlow", "3sec"),
    # 10000x, not 100x: the email container is capped at 100Mi and sits near 65Mi.
    # At 100x it leaks ~10KB/s, needing ~1h to OOM - far longer than a run, so the
    # Exit Code 137 this scenario is built around would never appear. 10000x reaches
    # the limit during the warmup window, producing a real OOMKill to diagnose.
    "emailMemoryLeak": ("emailMemoryLeak", "10000x"),
    "badDeploy1405": None,
    "episodicRecurrence": None,
    "poisonedEntity": None,
}

# Cleared before every injection so scenarios can never contaminate each other.
# postgresFailure (fail fast) and postgresSlow (pg_sleep delay) are mutually
# exclusive on the same dependency - with both on, slowness is unobservable.
CHAOS_FLAGS = [
    "postgresFailure",
    "postgresSlow",
    "emailMemoryLeak",
    "productCatalogFailure",
    "cartFailure",
    "paymentFailure",
    "paymentUnreachable",
    "adHighCpu",
    "adFailure",
    "kafkaQueueProblems",
    "recommendationCacheFailure",
    "imageSlowLoad",
    "intlShippingSlowdown",
    "failedReadinessProbe",
]


class InjectionError(RuntimeError):
    """Raised when a fault could not be injected; the caller decides whether to abort."""


def _read_flag_config(ns: str) -> dict:
    # Read the whole object rather than a jsonpath: the key contains dots
    # ("demo.flagd.json") which jsonpath would otherwise treat as nested fields.
    code, stdout, stderr = run_cmd(
        ["kubectl", "-n", ns, "get", "cm", CONFIGMAP, "-o", "json"],
        timeout=20,
    )
    if code != 0 or not stdout:
        raise InjectionError(
            f"Could not read configmap '{CONFIGMAP}' in namespace '{ns}': "
            f"{stderr or 'empty response'}"
        )
    try:
        data = json.loads(stdout).get("data", {})
    except json.JSONDecodeError as err:
        raise InjectionError(f"Could not parse configmap '{CONFIGMAP}': {err}")

    raw = data.get(CM_KEY)
    if not raw:
        raise InjectionError(
            f"Configmap '{CONFIGMAP}' in '{ns}' has no key '{CM_KEY}'. Present: {list(data)}"
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise InjectionError(f"Flag config in '{CONFIGMAP}' is not valid JSON: {err}")


def _apply_flag_config(ns: str, cfg: dict) -> None:
    # A merge patch keeps the ConfigMap's other keys intact, and passing the patch
    # as a single argv element avoids any shell quoting of the embedded JSON.
    patch = json.dumps({"data": {CM_KEY: json.dumps(cfg, indent=2)}})
    code, _, stderr = run_cmd(
        ["kubectl", "-n", ns, "patch", "cm", CONFIGMAP, "--type", "merge", "-p", patch],
        timeout=30,
    )
    if code != 0:
        if "forbidden" in (stderr or "").lower():
            raise InjectionError(
                f"Not permitted to patch configmap '{CONFIGMAP}' in namespace '{ns}'.\n"
                f"   The workshop service account needs 'configmaps: patch' on this namespace.\n"
                f"   Ask your facilitator to widen 'workshop-agent-role', or re-run with --no-inject\n"
                f"   if the fault has already been staged for you.\n"
                f"   kubectl said: {stderr}"
            )
        raise InjectionError(f"Failed to patch '{CONFIGMAP}' in '{ns}': {stderr}")


def _restart_flagd(ns: str) -> None:
    # flagd serves from an emptyDir that an initContainer copies the ConfigMap
    # into, so a ConfigMap patch alone changes nothing until the pod is recreated.
    # 30s proved too tight in practice - the API server can be slow to accept the
    # patch while other pods in the namespace are churning, and a timeout here aborts
    # an otherwise healthy run. One retry absorbs a transient slow response.
    last_err = ""
    for attempt in (1, 2):
        code, _, stderr = run_cmd(["kubectl", "-n", ns, "rollout", "restart", "deployment/flagd"], timeout=90)
        if code == 0:
            break
        last_err = stderr
        if attempt == 1:
            print(f"  ⚠️ flagd restart did not complete ({stderr or 'timeout'}); retrying once...")
    else:
        raise InjectionError(f"Could not restart flagd in '{ns}': {last_err}")

    code, _, stderr = run_cmd(
        ["kubectl", "-n", ns, "rollout", "status", "deployment/flagd", "--timeout=120s"],
        timeout=180,
    )
    if code != 0:
        raise InjectionError(f"flagd did not become ready in '{ns}': {stderr}")


def _set_flags(ns: str, target: Optional[Tuple[str, str]]) -> dict:
    """Clear every chaos flag, then enable `target` if one is given. Returns active flags."""
    cfg = _read_flag_config(ns)
    flags = cfg.get("flags")
    if not isinstance(flags, dict):
        raise InjectionError(f"Unexpected flag config shape in '{CONFIGMAP}': no 'flags' object.")

    for name in CHAOS_FLAGS:
        spec = flags.get(name)
        if isinstance(spec, dict):
            variants = spec.get("variants", {})
            spec["defaultVariant"] = "off" if "off" in variants else next(iter(variants), "off")

    active: dict = {}
    if target:
        flag_name, variant = target
        spec = flags.get(flag_name)
        if not isinstance(spec, dict):
            raise InjectionError(f"Flag '{flag_name}' is not present in this namespace's flagd config.")
        variants = spec.get("variants", {})
        if variant not in variants:
            raise InjectionError(
                f"Variant '{variant}' is not valid for flag '{flag_name}'. Available: {list(variants)}"
            )
        spec["defaultVariant"] = variant
        spec["state"] = "ENABLED"
        active[flag_name] = variant

    _apply_flag_config(ns, cfg)
    _restart_flagd(ns)
    return active


def inject_scenario(namespace: str, scenario: str, warmup_s: int = 45) -> Optional[Tuple[str, str]]:
    """Enable the fault for `scenario`, clearing all others. Returns the fault set, or None."""
    ns = validate_k8s_name(namespace, "namespace")
    if scenario not in SCENARIO_FAULTS:
        raise InjectionError(
            f"Unknown scenario '{scenario}'. Valid: {', '.join(sorted(SCENARIO_FAULTS))}"
        )

    target = SCENARIO_FAULTS[scenario]
    if target is None:
        print(f"  ℹ️  Scenario '{scenario}' needs no injected cluster fault "
              f"(driven by deploy history, episodic memory, or a non-existent service).")
        print("  ▶ Clearing any previously injected faults so they cannot interfere...")
        _set_flags(ns, None)
        print("  ✅ Namespace is clean.")
        return None

    flag_name, variant = target
    print(f"  ▶ Injecting fault for '{scenario}': {flag_name} = {variant} (namespace '{ns}')...")
    _set_flags(ns, target)
    print(f"  ✅ Fault active: {flag_name}={variant}")

    if warmup_s > 0:
        # Symptoms only exist once the load generator has driven traffic through the
        # faulted path; investigating immediately would show a still-healthy cluster.
        print(f"  ⏳ Waiting {warmup_s}s for traffic to generate observable symptoms...")
        time.sleep(warmup_s)
    return target


def clear_faults(namespace: str) -> None:
    """Turn every chaos flag off, leaving the namespace healthy for the next run."""
    ns = validate_k8s_name(namespace, "namespace")
    print(f"  ▶ Clearing injected faults in namespace '{ns}'...")
    _set_flags(ns, None)
    print("  ✅ Faults cleared - namespace is healthy again.")


def active_faults(namespace: str) -> dict:
    """Report which chaos flags are currently enabled (read-only)."""
    ns = validate_k8s_name(namespace, "namespace")
    flags = _read_flag_config(ns).get("flags", {})
    active = {}
    for name, spec in flags.items():
        if name.startswith("loadGenerator") or not isinstance(spec, dict):
            continue
        default = spec.get("defaultVariant")
        if default not in ("off", 0, "0", False, None):
            active[name] = default
    return active
