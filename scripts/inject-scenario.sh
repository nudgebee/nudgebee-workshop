#!/usr/bin/env bash
# ==============================================================================
# NudgeBee SRE & AIOps Workshop · Scenario Fault Injector
# ==============================================================================
# Turns a workshop scenario into a REAL fault in the cluster, for facilitators
# staging a fault ahead of time (attendees then run the agent with --no-inject).
#
# mini_agent.py injects and clears faults itself, so attendees never need this.
# It delegates to agent/fault_injection.py so both paths stage identical faults.
#
# Usage:
#   ./scripts/inject-scenario.sh --scenario postgresFailure [--namespace group-1]
#   ./scripts/inject-scenario.sh --reset               # clear all injected faults
#   ./scripts/inject-scenario.sh --status              # show what is currently on
# ==============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-group-1}"
SCENARIO=""
ACTION="inject"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario) SCENARIO="$2"; shift 2 ;;
    --namespace|-n) NAMESPACE="$2"; shift 2 ;;
    --reset) ACTION="reset"; shift ;;
    --status) ACTION="status"; shift ;;
    -h|--help) sed -n '2,16p' "$0" | cut -c 3-; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ "$ACTION" == "inject" && -z "$SCENARIO" ]]; then
  echo "ERROR: --scenario is required (or use --reset / --status)." >&2
  exit 1
fi

cd "${REPO_ROOT}/agent"
ACTION="$ACTION" NAMESPACE="$NAMESPACE" SCENARIO="$SCENARIO" python3 -c '
import os, sys
import fault_injection as fi

ns, scenario, action = os.environ["NAMESPACE"], os.environ["SCENARIO"], os.environ["ACTION"]
try:
    if action == "inject":
        fi.inject_scenario(ns, scenario, warmup_s=0)
        print(f"\nDONE: Fault staged for {scenario}. Allow ~60s for traffic to produce symptoms, then run:")
        print(f"      cd agent && python3 mini_agent.py --scenario {scenario} --no-inject")
    elif action == "reset":
        fi.clear_faults(ns)
    active = fi.active_faults(ns)
except fi.InjectionError as err:
    sys.exit(f"ERROR: {err}")
print("\nACTIVE FAULTS:", ", ".join(f"{k}={v}" for k, v in active.items()) if active else "none - cluster is healthy")
'
