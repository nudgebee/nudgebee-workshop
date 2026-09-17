#!/usr/bin/env bash
# ==============================================================================
# NudgeBee SRE & AIOps Workshop · Scenario Fault Injector
# ==============================================================================
# Turns a workshop scenario into a REAL fault in the cluster.
#
# The agent's --scenario flag only changes its prompt and its grading criteria;
# it does not break anything. Without this script the agent investigates a
# healthy cluster, finds nothing, and is marked as failing. Run this first.
#
# Usage:
#   ./scripts/inject-scenario.sh --scenario postgresFailure [--namespace group-1]
#   ./scripts/inject-scenario.sh --reset               # clear all injected faults
#   ./scripts/inject-scenario.sh --status              # show what is currently on
# ==============================================================================

set -euo pipefail

NAMESPACE="${NAMESPACE:-group-1}"
SCENARIO=""
ACTION="inject"
CONFIGMAP="flagd-config"
CM_KEY="demo.flagd.json"

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

# Scenario -> flagd flag + variant. Scenarios absent from this map need no
# cluster fault: they are driven by rollout history, episodic memory, or a
# deliberately non-existent service name.
flag_for_scenario() {
  case "$1" in
    postgresFailure)  echo "postgresFailure on" ;;
    postgresSlow)     echo "postgresSlow 3sec" ;;
    emailMemoryLeak)  echo "emailMemoryLeak 100x" ;;
    badDeploy1405|episodicRecurrence|poisonedEntity) echo "NONE" ;;
    *) echo "UNKNOWN" ;;
  esac
}

CHAOS_FLAGS="postgresFailure postgresSlow emailMemoryLeak productCatalogFailure cartFailure paymentFailure adHighCpu kafkaQueueProblems"

show_status() {
  echo "▶ Current fault state in namespace '${NAMESPACE}':"
  kubectl -n "${NAMESPACE}" get cm "${CONFIGMAP}" -o json \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)['data']
flags = json.loads(data['${CM_KEY}'])['flags']
active = []
for name, spec in sorted(flags.items()):
    if name.startswith('loadGenerator'):
        continue
    default = spec.get('defaultVariant')
    off = default in ('off', 0, '0', False, None)
    if not off:
        active.append((name, default))
    print(f\"  {'ACTIVE ->' if not off else '         '} {name:28} {default}\")
print()
print('ACTIVE FAULTS:', ', '.join(f'{n}={v}' for n, v in active) if active else 'none - cluster is healthy')
"
}

if [[ "$ACTION" == "status" ]]; then
  show_status
  exit 0
fi

TARGET_FLAG=""
TARGET_VARIANT=""

if [[ "$ACTION" == "inject" ]]; then
  if [[ -z "$SCENARIO" ]]; then
    echo "ERROR: --scenario is required (or use --reset / --status)." >&2
    exit 1
  fi
  MAPPING="$(flag_for_scenario "$SCENARIO")"
  if [[ "$MAPPING" == "UNKNOWN" ]]; then
    echo "ERROR: Unknown scenario '${SCENARIO}'." >&2
    echo "   Valid: badDeploy1405 episodicRecurrence postgresFailure emailMemoryLeak postgresSlow poisonedEntity" >&2
    exit 1
  fi
  if [[ "$MAPPING" == "NONE" ]]; then
    echo "NOTE: Scenario '${SCENARIO}' needs no injected cluster fault."
    echo "      It is driven by deploy history, episodic memory, or a non-existent service name."
    echo "      Clearing any previously injected faults so they do not interfere..."
    ACTION="reset"
  else
    TARGET_FLAG="${MAPPING%% *}"
    TARGET_VARIANT="${MAPPING##* }"
  fi
fi

echo "> Updating '${CONFIGMAP}' in namespace '${NAMESPACE}'..."
CURRENT_JSON="$(kubectl -n "${NAMESPACE}" get cm "${CONFIGMAP}" -o jsonpath="{.data.${CM_KEY//./\\.}}")"

if [[ -z "$CURRENT_JSON" ]]; then
  echo "ERROR: Could not read ${CM_KEY} from configmap ${CONFIGMAP}." >&2
  exit 1
fi

UPDATED_JSON="$(printf '%s' "$CURRENT_JSON" | ACTION="$ACTION" TARGET_FLAG="$TARGET_FLAG" TARGET_VARIANT="$TARGET_VARIANT" CHAOS_FLAGS="$CHAOS_FLAGS" python3 -c "
import sys, json, os

cfg = json.load(sys.stdin)
flags = cfg['flags']

# Always start from a clean slate so scenarios never contaminate each other.
for name in os.environ['CHAOS_FLAGS'].split():
    if name in flags:
        variants = flags[name].get('variants', {})
        flags[name]['defaultVariant'] = 'off' if 'off' in variants else list(variants)[0]

if os.environ['ACTION'] == 'inject':
    name = os.environ['TARGET_FLAG']
    variant = os.environ['TARGET_VARIANT']
    if name not in flags:
        sys.exit(f\"flag '{name}' not present in flagd config\")
    if variant not in flags[name].get('variants', {}):
        sys.exit(f\"variant '{variant}' not valid for '{name}'; have {list(flags[name]['variants'])}\")
    flags[name]['defaultVariant'] = variant
    flags[name]['state'] = 'ENABLED'

json.dump(cfg, sys.stdout, indent=2)
")"

kubectl -n "${NAMESPACE}" create configmap "${CONFIGMAP}" \
  --from-literal="${CM_KEY}=${UPDATED_JSON}" \
  --dry-run=client -o yaml | kubectl -n "${NAMESPACE}" apply -f - >/dev/null

# flagd serves from an emptyDir that an initContainer copies the ConfigMap into,
# so the new config only takes effect once the pod is recreated.
echo "> Restarting flagd so it picks up the new configuration..."
kubectl -n "${NAMESPACE}" rollout restart deployment/flagd >/dev/null
kubectl -n "${NAMESPACE}" rollout status deployment/flagd --timeout=120s

echo ""
if [[ "$ACTION" == "reset" ]]; then
  echo "DONE: All injected faults cleared - cluster is healthy."
else
  echo "DONE: Fault injected: ${TARGET_FLAG} = ${TARGET_VARIANT}  (scenario '${SCENARIO}')"
  echo "      Allow ~60s for traffic to generate symptoms, then run:"
  echo "      cd agent && python3 mini_agent.py --scenario ${SCENARIO}"
fi
echo ""
show_status
