#!/usr/bin/env bash
# ==============================================================================
# NudgeBee SRE & AIOps Workshop · Attendee Team Bootstrap Script
# ==============================================================================
# Configures an attendee's Codespace or local environment for their assigned team.
#
# Usage:
#   ./scripts/bootstrap-team.sh --team <N> [OPTIONS]
#
# Options:
#   --team <N>                Team number (e.g. 1, 2, 3) -> maps to group-N
#   --kubeconfig <PATH>       Path or URL to your team kubeconfig file
#   --api-key <KEY>           LLM Gateway / OpenAI API key
#   -h, --help                Show this help message
# ==============================================================================

set -euo pipefail

TEAM=""
KUBECONFIG_SOURCE=""
API_KEY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --team)
      TEAM="$2"
      shift 2
      ;;
    --kubeconfig)
      KUBECONFIG_SOURCE="$2"
      shift 2
      ;;
    --api-key)
      API_KEY="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,16p' "$0" | cut -c 3-
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$TEAM" && -z "$KUBECONFIG_SOURCE" ]]; then
  echo "❌ Error: Please specify your team number: ./scripts/bootstrap-team.sh --team <N>" >&2
  exit 1
fi

TARGET_NS="group-${TEAM}"
mkdir -p "${HOME}/.kube"

echo "=========================================================================="
echo "🐝 CONFIGURING ENVIRONMENT FOR TEAM: ${TARGET_NS}"
echo "=========================================================================="

# 1. Handle Kubeconfig
if [[ -n "$KUBECONFIG_SOURCE" ]]; then
  if [[ "$KUBECONFIG_SOURCE" =~ ^https?:// ]]; then
    echo "▶ Downloading kubeconfig from ${KUBECONFIG_SOURCE}..."
    curl -sSL "$KUBECONFIG_SOURCE" -o "${HOME}/.kube/config"
  elif [[ -f "$KUBECONFIG_SOURCE" ]]; then
    echo "▶ Installing kubeconfig from ${KUBECONFIG_SOURCE}..."
    cp "$KUBECONFIG_SOURCE" "${HOME}/.kube/config"
  else
    echo "❌ Error: File not found: ${KUBECONFIG_SOURCE}" >&2
    exit 1
  fi
  chmod 600 "${HOME}/.kube/config"
elif [[ -f "./workshop-credentials/kubeconfig-${TARGET_NS}.yaml" ]]; then
  echo "▶ Using local credential: ./workshop-credentials/kubeconfig-${TARGET_NS}.yaml..."
  cp "./workshop-credentials/kubeconfig-${TARGET_NS}.yaml" "${HOME}/.kube/config"
  chmod 600 "${HOME}/.kube/config"
else
  echo "⚠️ Note: No kubeconfig found in default location. Ensure ~/.kube/config is populated."
fi

# 2. Update agent/config.yaml namespace
CONFIG_FILE="./agent/config.yaml"
if [[ -f "$CONFIG_FILE" ]]; then
  echo "▶ Updating default namespace to '${TARGET_NS}' in ${CONFIG_FILE}..."
  if command -v sed >/dev/null 2>&1; then
    sed -i.bak -E "s/^namespace: \".*\"/namespace: \"${TARGET_NS}\"/" "$CONFIG_FILE" && rm -f "${CONFIG_FILE}.bak"
  fi
fi

# 3. Handle API Key
if [[ -n "$API_KEY" ]]; then
  export OPENAI_API_KEY="$API_KEY"
  if [[ -f "${HOME}/.bashrc" ]]; then
    if ! grep -q "OPENAI_API_KEY" "${HOME}/.bashrc"; then
      echo "export OPENAI_API_KEY=\"${API_KEY}\"" >> "${HOME}/.bashrc"
    fi
  fi
  echo "▶ OPENAI_API_KEY configured."
fi

# 4. Verify Cluster Connectivity (if kubectl & config present)
if command -v kubectl >/dev/null 2>&1 && [[ -f "${HOME}/.kube/config" ]]; then
  echo ""
  echo "▶ Testing cluster connectivity in namespace '${TARGET_NS}'..."
  if kubectl get pods -n "${TARGET_NS}" --request-timeout='5s' >/dev/null 2>&1; then
    echo "✅ Cluster connectivity verified! Active pods:"
    kubectl get pods -n "${TARGET_NS}" --no-headers | head -n 5 || true
  else
    echo "⚠️ Warning: Unable to reach cluster or list pods in '${TARGET_NS}'."
  fi
fi

echo ""
echo "=========================================================================="
echo "🎉 TEAM ${TARGET_NS} READY!"
echo "=========================================================================="
echo "Next Steps:"
echo "  1. Test your agent tools:  python3 agent/mini_agent.py --test-tools"
echo "  2. Run offline simulation: python3 agent/mini_agent.py --scenario badDeploy1405 --model mock"
echo "=========================================================================="
