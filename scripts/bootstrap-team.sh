#!/usr/bin/env bash
# ==============================================================================
# NudgeBee SRE & AIOps Workshop · Attendee Team Bootstrap Script
# ==============================================================================
# Configures an attendee's Codespace or local machine in 1 step.
# Supports encrypted team bundles (.enc), direct kubeconfigs, or interactive setup.
#
# Usage:
#   ./scripts/bootstrap-team.sh [OPTIONS]
#
# Options:
#   --team <N>                Team number (1, 2, 3...) -> maps to group-N
#   --pass <PASS>             Room passphrase to decrypt your team bundle (team-N.enc)
#   --url <URL>               Base URL of cloud storage hosting team-N.enc bundles
#   --kubeconfig <PATH>       Direct path or URL to unencrypted kubeconfig file
#   --api-key <KEY>           LLM Gateway / OpenAI API key
#   -h, --help                Show this help message
# ==============================================================================

set -euo pipefail

TEAM=""
PASSPHRASE=""
STORAGE_URL=""
KUBECONFIG_SOURCE=""
API_KEY=""

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --team)
      TEAM="$2"
      shift 2
      ;;
    --pass)
      PASSPHRASE="$2"
      shift 2
      ;;
    --url)
      STORAGE_URL="${2%/}"
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
      sed -n '2,18p' "$0" | cut -c 3-
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# ------------------------------------------------------------------------------
# Interactive Mode if arguments are omitted
# ------------------------------------------------------------------------------
if [[ -z "$TEAM" && -z "$KUBECONFIG_SOURCE" ]]; then
  echo "=========================================================================="
  echo "🐝 NUDGEBEE SRE & AIOPS WORKSHOP · TEAM SETUP"
  echo "=========================================================================="
  read -r -p "? Enter your assigned Team Number (1-10): " TEAM
  if [[ -z "$TEAM" ]]; then
    echo "❌ Team number is required." >&2
    exit 1
  fi

  read -r -s -p "? Enter Room Passphrase (or press Enter if using local unencrypted file): " PASSPHRASE
  echo ""

  if [[ -n "$PASSPHRASE" && -z "$STORAGE_URL" ]]; then
    read -r -p "? Cloud Storage Base URL (press Enter to check local folder): " STORAGE_URL
    STORAGE_URL="${STORAGE_URL%/}"
  fi
fi

TARGET_NS="group-${TEAM}"
mkdir -p "${HOME}/.kube"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

echo ""
echo "=========================================================================="
echo "🐝 CONFIGURING ENVIRONMENT FOR TEAM: ${TARGET_NS}"
echo "=========================================================================="

# ------------------------------------------------------------------------------
# 1. Handle Encrypted Bundle (.enc) if Passphrase is provided
# ------------------------------------------------------------------------------
if [[ -n "$PASSPHRASE" ]]; then
  ENC_FILE=""
  # Case A: Download from Storage URL
  if [[ -n "$STORAGE_URL" ]]; then
    REMOTE_URL="${STORAGE_URL}/team-${TEAM}.enc"
    echo "▶ Downloading encrypted bundle from ${REMOTE_URL}..."
    if curl -f -sSL "${REMOTE_URL}" -o "${TMP_DIR}/bundle.enc"; then
      ENC_FILE="${TMP_DIR}/bundle.enc"
    else
      echo "❌ Error: Failed to download ${REMOTE_URL}. Check your storage URL and team number." >&2
      exit 1
    fi
  # Case B: Local workshop-credentials directory
  elif [[ -f "./workshop-credentials/team-${TEAM}.enc" ]]; then
    echo "▶ Using local encrypted bundle: ./workshop-credentials/team-${TEAM}.enc..."
    ENC_FILE="./workshop-credentials/team-${TEAM}.enc"
  elif [[ -f "./team-${TEAM}.enc" ]]; then
    echo "▶ Using local encrypted bundle: ./team-${TEAM}.enc..."
    ENC_FILE="./team-${TEAM}.enc"
  else
    echo "❌ Error: Passphrase provided, but no bundle found at ./workshop-credentials/team-${TEAM}.enc or via --url." >&2
    exit 1
  fi

  echo "▶ Decrypting team bundle with room passphrase..."
  if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -in "${ENC_FILE}" -out "${TMP_DIR}/payload.json" -pass pass:"${PASSPHRASE}" 2>/dev/null; then
    echo "❌ Decryption failed! Check the room passphrase." >&2
    exit 1
  fi

  # Extract kubeconfig and api_key from JSON
  python3 -c "
import json, sys, os

with open(sys.argv[1], 'r') as f:
    data = json.load(f)

# Write kubeconfig
kube_path = os.path.expanduser('~/.kube/config')
with open(kube_path, 'w') as kf:
    kf.write(data.get('kubeconfig', ''))

# Write api_key if present
api_key = data.get('api_key', '')
if api_key:
    with open(sys.argv[2], 'w') as af:
        af.write(api_key)
" "${TMP_DIR}/payload.json" "${TMP_DIR}/extracted_key.txt"

  chmod 600 "${HOME}/.kube/config"
  echo "✅ Kubeconfig decrypted and installed to ~/.kube/config"

  if [[ -f "${TMP_DIR}/extracted_key.txt" ]]; then
    API_KEY=$(cat "${TMP_DIR}/extracted_key.txt")
  fi

# ------------------------------------------------------------------------------
# 2. Handle Direct Kubeconfig (Unencrypted fallback)
# ------------------------------------------------------------------------------
else
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
  elif [[ -f "./workshop-credentials/kubeconfig-${TARGET_NS}.yaml" ]]; then
    echo "▶ Using local credential: ./workshop-credentials/kubeconfig-${TARGET_NS}.yaml..."
    cp "./workshop-credentials/kubeconfig-${TARGET_NS}.yaml" "${HOME}/.kube/config"
  fi
  chmod 600 "${HOME}/.kube/config" 2>/dev/null || true
fi

# ------------------------------------------------------------------------------
# 3. Update agent/config.yaml namespace to team namespace
# ------------------------------------------------------------------------------
CONFIG_FILE="./agent/config.yaml"
if [[ -f "$CONFIG_FILE" ]]; then
  echo "▶ Setting target namespace to '${TARGET_NS}' in ${CONFIG_FILE}..."
  if command -v sed >/dev/null 2>&1; then
    sed -i.bak -E "s/^namespace: \".*\"/namespace: \"${TARGET_NS}\"/" "$CONFIG_FILE" && rm -f "${CONFIG_FILE}.bak"
  fi
fi

# ------------------------------------------------------------------------------
# 4. Configure OPENAI_API_KEY
# ------------------------------------------------------------------------------
if [[ -n "$API_KEY" ]]; then
  export OPENAI_API_KEY="$API_KEY"
  BASHRC="${HOME}/.bashrc"
  ZSHRC="${HOME}/.zshrc"

  if [[ -f "$BASHRC" ]]; then
    if ! grep -q "OPENAI_API_KEY=" "$BASHRC"; then
      echo "export OPENAI_API_KEY=\"${API_KEY}\"" >> "$BASHRC"
    else
      sed -i.bak -E "s/export OPENAI_API_KEY=\".*\"/export OPENAI_API_KEY=\"${API_KEY}\"/" "$BASHRC" && rm -f "${BASHRC}.bak"
    fi
  fi
  if [[ -f "$ZSHRC" ]]; then
    if ! grep -q "OPENAI_API_KEY=" "$ZSHRC"; then
      echo "export OPENAI_API_KEY=\"${API_KEY}\"" >> "$ZSHRC"
    else
      sed -i.bak -E "s/export OPENAI_API_KEY=\".*\"/export OPENAI_API_KEY=\"${API_KEY}\"/" "$ZSHRC" && rm -f "${ZSHRC}.bak"
    fi
  fi
  echo "✅ LLM Gateway API key configured in environment and shell profile."
fi

# ------------------------------------------------------------------------------
# 5. Verify Cluster Connectivity
# ------------------------------------------------------------------------------
if command -v kubectl >/dev/null 2>&1 && [[ -f "${HOME}/.kube/config" ]]; then
  echo ""
  echo "▶ Testing live cluster connectivity in namespace '${TARGET_NS}'..."
  if kubectl get pods -n "${TARGET_NS}" --request-timeout='5s' >/dev/null 2>&1; then
    echo "✅ Cluster connectivity verified! Active pods:"
    kubectl get pods -n "${TARGET_NS}" --no-headers | head -n 6 || true
  else
    echo "⚠️ Note: Could not query pods in '${TARGET_NS}'. Verify cluster availability."
  fi
fi

echo ""
echo "=========================================================================="
echo "🎉 TEAM ${TARGET_NS} SETUP COMPLETE!"
echo "=========================================================================="
echo "Next Steps:"
echo "  1. Verify your diagnostic tools: python3 agent/mini_agent.py --test-tools"
echo "  2. Run your first simulation:    python3 agent/mini_agent.py --scenario badDeploy1405 --model mock"
echo "=========================================================================="
