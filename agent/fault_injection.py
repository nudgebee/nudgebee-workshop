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

# Scenario -> (flagd flag, variant). Scenarios mapped to None need no flagd
# fault: badDeploy1405 is staged as real rollout revisions instead (see
# _stage_bad_deploy) and poisonedEntity names a deliberately non-existent service.
SCENARIO_FAULTS: Dict[str, Optional[Tuple[str, str]]] = {
    "postgresFailure": ("postgresFailure", "on"),
    "postgresSlow": ("postgresSlow", "3sec"),
    # 10000x, not 100x: the email container is capped at 100Mi and sits near 65Mi.
    # At 100x it leaks ~10KB/s, needing ~1h to OOM - far longer than a run, so the
    # Exit Code 137 this scenario is built around would never appear. Even at 10000x
    # the leak is traffic-driven and takes a few minutes, so injection waits for the
    # OOMKill itself (see SYMPTOM_WAITS) rather than a fixed warmup.
    "emailMemoryLeak": ("emailMemoryLeak", "10000x"),
    "badDeploy1405": None,
    # A recurrence needs a live failure to recur: without one the agent can only
    # recite memory against a healthy cluster, which is exactly the habit M8 warns against.
    "episodicRecurrence": ("postgresFailure", "on"),
    "poisonedEntity": None,
}

# The postgres flags are not in the chart's flagd-config. flagd's initContainer
# splices them into its own copy - but only when "postgresFailure" is absent from
# the ConfigMap, and it then exits 1 unless "postgresSlow" is also present. So the
# two must always be added together, with the same definitions the initContainer uses.
POSTGRES_FLAGS = {
    "postgresFailure": {
        "description": "Make the product-catalog PostgreSQL dependency unavailable - every product "
                       "query fails fast, driving a gRPC error rate that cascades to its callers",
        "state": "ENABLED",
        "variants": {"on": True, "off": False},
        "defaultVariant": "off",
    },
    "postgresSlow": {
        "description": "Make every product-catalog PostgreSQL query genuinely slow via pg_sleep - "
                       "variant value is the per-query delay in ms, visible as a slow DB span in traces",
        "state": "ENABLED",
        "variants": {"off": 0, "1sec": 1000, "3sec": 3000, "6sec": 6000},
        "defaultVariant": "off",
    },
}

# Scenarios whose symptom takes longer than the warmup to appear: (deployment,
# termination reason, max seconds). Injection polls until a container of the
# deployment has been terminated for that reason since the fault was enabled.
SYMPTOM_WAITS: Dict[str, Tuple[str, str, int]] = {
    "emailMemoryLeak": ("email", "OOMKilled", 240),
}

# badDeploy1405: two real rollouts on checkout so `kubectl rollout history` holds
# the evidence. Removed again by clear_faults, which rolls back to the original template.
BAD_DEPLOY_TARGET = "checkout"
BAD_DEPLOY_ENV = "PAYMENT_CLIENT_TIMEOUT"
BAD_DEPLOY_MARKER = "workshop.nudgebee.com/staged"
CHANGE_CAUSE = "kubernetes.io/change-cause"
BAD_DEPLOY_REVISIONS = [
    ("500ms", "infra-team@company.internal 11:30 UTC: Bump base container image to alpine:3.19"),
    ("5000ms", "payments@company.internal 14:05 UTC: Commit a7f39b1 'perf: tune client timeouts "
               "for downstream payment service' (payment_client_timeout 500ms -> 5000ms)"),
]

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
        if flag_name in POSTGRES_FLAGS and flag_name not in flags:
            # Add both together - see POSTGRES_FLAGS for why one alone breaks flagd.
            for name, definition in POSTGRES_FLAGS.items():
                flags.setdefault(name, json.loads(json.dumps(definition)))
        spec = flags.get(flag_name)
        if not isinstance(spec, dict):
            raise InjectionError(
                f"Flag '{flag_name}' is not present in namespace '{ns}'s flagd-config ConfigMap.\n"
                f"   This namespace was deployed without it. Ask your facilitator to add the\n"
                f"   flag to the flagd-config for this namespace."
            )
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


def _get_deployment(ns: str, name: str) -> dict:
    code, stdout, stderr = run_cmd(["kubectl", "-n", ns, "get", "deployment", name, "-o", "json"], timeout=20)
    if code != 0 or not stdout:
        raise InjectionError(f"Could not read deployment/{name} in '{ns}': {stderr or 'empty response'}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as err:
        raise InjectionError(f"Could not parse deployment/{name}: {err}")


def _rollout_checkout(ns: str, timeout_value: Optional[str], cause: str) -> None:
    """Roll checkout to a template with PAYMENT_CLIENT_TIMEOUT=timeout_value (None removes it)."""
    dep = _get_deployment(ns, BAD_DEPLOY_TARGET)
    template = dep["spec"]["template"]
    container = template["spec"]["containers"][0]
    env = [e for e in container.get("env", []) if e.get("name") != BAD_DEPLOY_ENV]
    if timeout_value is not None:
        env.append({"name": BAD_DEPLOY_ENV, "value": timeout_value})
    container["env"] = env
    annotations = template.setdefault("metadata", {}).setdefault("annotations", {})
    if timeout_value is None:
        annotations.pop(BAD_DEPLOY_MARKER, None)
    else:
        annotations[BAD_DEPLOY_MARKER] = timeout_value

    # A merge patch replaces the whole containers list, so send the full, edited list.
    # Removed annotations need an explicit null to be deleted by the merge.
    template_patch = {"metadata": {"annotations": {**annotations}}, "spec": {"containers": template["spec"]["containers"]}}
    if timeout_value is None:
        template_patch["metadata"]["annotations"][BAD_DEPLOY_MARKER] = None
    patch = json.dumps({
        "metadata": {"annotations": {CHANGE_CAUSE: cause}},
        "spec": {"template": template_patch},
    })
    # Same slow-API-server allowance as _restart_flagd: a longer timeout and one retry.
    # The patch is idempotent, so re-sending it after a timeout is safe.
    for attempt in (1, 2):
        code, _, stderr = run_cmd(
            ["kubectl", "-n", ns, "patch", "deployment", BAD_DEPLOY_TARGET, "--type", "merge", "-p", patch],
            timeout=90,
        )
        if code == 0:
            break
        if attempt == 1:
            print(f"  ⚠️ checkout patch did not complete ({stderr or 'timeout'}); retrying once...")
    else:
        raise InjectionError(f"Could not roll out deployment/{BAD_DEPLOY_TARGET} in '{ns}': {stderr}")
    code, _, stderr = run_cmd(
        ["kubectl", "-n", ns, "rollout", "status", f"deployment/{BAD_DEPLOY_TARGET}", "--timeout=180s"],
        timeout=200,
    )
    if code != 0:
        raise InjectionError(f"deployment/{BAD_DEPLOY_TARGET} did not become ready in '{ns}': {stderr}")


def _bad_deploy_staged(ns: str) -> bool:
    dep = _get_deployment(ns, BAD_DEPLOY_TARGET)
    annotations = dep["spec"]["template"].get("metadata", {}).get("annotations", {}) or {}
    return BAD_DEPLOY_MARKER in annotations


def _stage_bad_deploy(ns: str) -> None:
    """Create the badDeploy1405 evidence as real checkout revisions, oldest first."""
    for timeout_value, cause in BAD_DEPLOY_REVISIONS:
        print(f"  ▶ Rolling out checkout: {cause.split(': ', 1)[-1][:70]}...")
        _rollout_checkout(ns, timeout_value, cause)


def _revert_bad_deploy(ns: str) -> None:
    if _bad_deploy_staged(ns):
        print("  ▶ Rolling checkout back to its original configuration...")
        _rollout_checkout(ns, None, "workshop reset: revert to baseline configuration")


def _termination_count(ns: str, deployment: str, reason: str) -> int:
    """Restarts of the deployment's pods whose last termination matched `reason`."""
    code, stdout, _ = run_cmd(["kubectl", "-n", ns, "get", "pods", "-o", "json"], timeout=20)
    if code != 0 or not stdout:
        return 0
    total = 0
    for pod in json.loads(stdout).get("items", []):
        if not pod["metadata"]["name"].startswith(f"{deployment}-"):
            continue
        for status in pod.get("status", {}).get("containerStatuses", []):
            last = status.get("lastState", {}).get("terminated", {})
            if last.get("reason") == reason:
                total += status.get("restartCount", 0)
    return total


def _wait_for_symptom(ns: str, scenario: str, baseline: int) -> None:
    deployment, reason, limit = SYMPTOM_WAITS[scenario]
    print(f"  ⏳ Waiting for {deployment} to be {reason} (up to {limit // 60} min; the leak is traffic-driven)...")
    waited = 0
    while waited < limit:
        if _termination_count(ns, deployment, reason) > baseline:
            print(f"  ✅ Symptom observed: {deployment} container {reason} after ~{waited}s.")
            return
        time.sleep(15)
        waited += 15
    print(f"  ⚠️ {deployment} was not {reason} within {limit}s - the agent may only see the memory growth.")


def inject_scenario(namespace: str, scenario: str, warmup_s: int = 45) -> Optional[Tuple[str, str]]:
    """Enable the fault for `scenario`, clearing all others. Returns the fault set, or None."""
    ns = validate_k8s_name(namespace, "namespace")
    if scenario not in SCENARIO_FAULTS:
        raise InjectionError(
            f"Unknown scenario '{scenario}'. Valid: {', '.join(sorted(SCENARIO_FAULTS))}"
        )

    target = SCENARIO_FAULTS[scenario]
    if target is None:
        print("  ▶ Clearing any previously injected faults so they cannot interfere...")
        _set_flags(ns, None)
        if scenario == "badDeploy1405":
            _revert_bad_deploy(ns)
            _stage_bad_deploy(ns)
            print("  ✅ Bad deploy staged: checkout revision with commit a7f39b1 is live.")
            return ("badDeploy", BAD_DEPLOY_TARGET)
        print(f"  ℹ️  Scenario '{scenario}' needs no injected cluster fault (it names a non-existent service).")
        print("  ✅ Namespace is clean.")
        return None

    flag_name, variant = target
    baseline = _termination_count(ns, *SYMPTOM_WAITS[scenario][:2]) if scenario in SYMPTOM_WAITS else 0
    print(f"  ▶ Injecting fault for '{scenario}': {flag_name} = {variant} (namespace '{ns}')...")
    _set_flags(ns, target)
    print(f"  ✅ Fault active: {flag_name}={variant}")

    if warmup_s > 0:
        # Symptoms only exist once the load generator has driven traffic through the
        # faulted path; investigating immediately would show a still-healthy cluster.
        if scenario in SYMPTOM_WAITS:
            _wait_for_symptom(ns, scenario, baseline)
        else:
            print(f"  ⏳ Waiting {warmup_s}s for traffic to generate observable symptoms...")
            time.sleep(warmup_s)
    return target


def clear_faults(namespace: str) -> None:
    """Turn every chaos flag off and undo a staged bad deploy, leaving the namespace healthy."""
    ns = validate_k8s_name(namespace, "namespace")
    print(f"  ▶ Clearing injected faults in namespace '{ns}'...")
    _set_flags(ns, None)
    _revert_bad_deploy(ns)
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
    if _bad_deploy_staged(ns):
        active["badDeploy1405"] = f"{BAD_DEPLOY_TARGET} rolled out with {BAD_DEPLOY_ENV}"
    return active
