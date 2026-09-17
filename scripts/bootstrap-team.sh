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
#   --team <ID>               Team identifier (e.g. team-1, group-1, team-42)
#   --pass <PASS>             Room passphrase to decrypt your team bundle (<TEAM>.enc)
#   --url <URL>               Base URL of cloud storage hosting <TEAM>.enc bundles
#   --kubeconfig <PATH>       Direct path or URL to unencrypted kubeconfig file
#   --api-key <KEY>           LLM Gateway / OpenAI API key
#   -h, --help                Show this help message
# ==============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -z "$TEAM" ]]; then
        TEAM="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2
        exit 1
      fi
      ;;
  esac
done

# Default workshop storage bucket if not explicitly overridden
DEFAULT_STORAGE_URL="https://storage.googleapis.com/nudgebee-hyd-workshop-2026-09-18"
if [[ -z "$STORAGE_URL" ]]; then
  STORAGE_URL="${DEFAULT_STORAGE_URL}"
fi

# ------------------------------------------------------------------------------
# Interactive Mode if arguments are omitted
# ------------------------------------------------------------------------------
if [[ -z "$TEAM" && -z "$KUBECONFIG_SOURCE" ]]; then
  echo "=========================================================================="
  echo "🐝 NUDGEBEE SRE & AIOPS WORKSHOP · TEAM SETUP"
  echo "=========================================================================="
  read -r -p "? Enter your assigned Team ID (e.g. 1 or team-1): " TEAM
  if [[ -z "$TEAM" ]]; then
    echo "❌ Team ID is required." >&2
    exit 1
  fi
fi

if [[ -z "$KUBECONFIG_SOURCE" && -z "$PASSPHRASE" ]]; then
  read -r -s -p "? Enter Room Passphrase (or press Enter if using local unencrypted file): " PASSPHRASE
  echo ""
fi

TARGET_NS="${TEAM}"
if [[ ! "$TEAM" =~ ^(group|team)- ]]; then
  TARGET_NS="group-${TEAM}"
fi

DEST_KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config}"
mkdir -p "$(dirname "${DEST_KUBECONFIG}")"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

echo ""
echo "=========================================================================="
echo "🐝 CONFIGURING ENVIRONMENT FOR TEAM: ${TEAM}"
echo "=========================================================================="

# ------------------------------------------------------------------------------
# 1. Handle Encrypted Bundle (.enc) if Passphrase is provided
# ------------------------------------------------------------------------------
if [[ -n "$PASSPHRASE" ]]; then
  ENC_FILE=""
  BUNDLE_NAME="${TEAM}.enc"

  # Case A: Download from Storage URL if provided
  if [[ -n "$STORAGE_URL" ]]; then
    REMOTE_URL="${STORAGE_URL}/${BUNDLE_NAME}"
    echo "▶ Downloading encrypted bundle from ${REMOTE_URL}..."
    if curl -f -sSL "${REMOTE_URL}" -o "${TMP_DIR}/${BUNDLE_NAME}" 2>/dev/null; then
      ENC_FILE="${TMP_DIR}/${BUNDLE_NAME}"
    else
      # If TEAM has a prefix like team-1 or group-1, also check numeric ID (e.g. 1.enc)
      ALT_NAME=""
      if [[ "$TEAM" =~ ^(team|group)-([0-9]+)$ ]]; then
        ALT_NAME="${BASH_REMATCH[2]}.enc"
      elif [[ "$TEAM" =~ ^[0-9]+$ ]]; then
        ALT_NAME="team-${TEAM}.enc"
      fi
      if [[ -n "$ALT_NAME" ]] && curl -f -sSL "${STORAGE_URL}/${ALT_NAME}" -o "${TMP_DIR}/${ALT_NAME}" 2>/dev/null; then
        ENC_FILE="${TMP_DIR}/${ALT_NAME}"
        echo "✅ Downloaded bundle using alternative ID: ${ALT_NAME}"
      else
        echo "⚠️ Could not download from ${REMOTE_URL}. Checking local directory..."
      fi
    fi
  fi

  # Case B: Local workshop-credentials directory or root
  if [[ -z "$ENC_FILE" ]]; then
    if [[ -f "${REPO_ROOT}/workshop-credentials/${BUNDLE_NAME}" ]]; then
      echo "▶ Using local encrypted bundle: ${REPO_ROOT}/workshop-credentials/${BUNDLE_NAME}..."
      ENC_FILE="${REPO_ROOT}/workshop-credentials/${BUNDLE_NAME}"
    elif [[ -f "./workshop-credentials/${BUNDLE_NAME}" ]]; then
      echo "▶ Using local encrypted bundle: ./workshop-credentials/${BUNDLE_NAME}..."
      ENC_FILE="./workshop-credentials/${BUNDLE_NAME}"
    elif [[ -f "${REPO_ROOT}/${BUNDLE_NAME}" ]]; then
      echo "▶ Using local encrypted bundle: ${REPO_ROOT}/${BUNDLE_NAME}..."
      ENC_FILE="${REPO_ROOT}/${BUNDLE_NAME}"
    elif [[ -f "./${BUNDLE_NAME}" ]]; then
      echo "▶ Using local encrypted bundle: ./${BUNDLE_NAME}..."
      ENC_FILE="./${BUNDLE_NAME}"
    fi
  fi

  if [[ -z "$ENC_FILE" ]]; then
    echo "❌ Error: Could not find bundle '${BUNDLE_NAME}' at ${STORAGE_URL:-'(no storage URL provided)'} or locally." >&2
    exit 1
  fi

  echo "▶ Decrypting team bundle with room passphrase..."
  if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -in "${ENC_FILE}" -out "${TMP_DIR}/payload.json" -pass pass:"${PASSPHRASE}" 2>/dev/null; then
    echo "❌ Decryption failed! Check the room passphrase." >&2
    exit 1
  fi

  # Extract kubeconfig, api_key, and namespace from JSON
  python3 -c "
import json, sys, os

with open(sys.argv[1], 'r') as f:
    data = json.load(f)

# Write kubeconfig
kube_path = sys.argv[2]
with open(kube_path, 'w') as kf:
    kf.write(data.get('kubeconfig', ''))

# Write api_key if present
api_key = data.get('api_key', '')
if api_key:
    with open(sys.argv[3], 'w') as af:
        af.write(api_key)

# Write namespace if present
namespace = data.get('namespace', '')
if namespace:
    with open(sys.argv[4], 'w') as nf:
        nf.write(namespace)
" "${TMP_DIR}/payload.json" "${DEST_KUBECONFIG}" "${TMP_DIR}/extracted_key.txt" "${TMP_DIR}/extracted_ns.txt"

  chmod 600 "${DEST_KUBECONFIG}"
  echo "✅ Kubeconfig decrypted and installed to ${DEST_KUBECONFIG}"

  if [[ -f "${TMP_DIR}/extracted_key.txt" ]]; then
    API_KEY=$(cat "${TMP_DIR}/extracted_key.txt")
  fi

  if [[ -f "${TMP_DIR}/extracted_ns.txt" ]]; then
    EXTRACTED_NS=$(cat "${TMP_DIR}/extracted_ns.txt" | xargs)
    if [[ -n "$EXTRACTED_NS" ]]; then
      TARGET_NS="$EXTRACTED_NS"
    fi
  fi

# ------------------------------------------------------------------------------
# 2. Handle Direct Kubeconfig (Unencrypted fallback)
# ------------------------------------------------------------------------------
else
  if [[ -n "$KUBECONFIG_SOURCE" ]]; then
    if [[ "$KUBECONFIG_SOURCE" =~ ^https?:// ]]; then
      echo "▶ Downloading kubeconfig from ${KUBECONFIG_SOURCE}..."
      curl -sSL "$KUBECONFIG_SOURCE" -o "${DEST_KUBECONFIG}"
    elif [[ -f "$KUBECONFIG_SOURCE" ]]; then
      echo "▶ Installing kubeconfig from ${KUBECONFIG_SOURCE}..."
      cp "$KUBECONFIG_SOURCE" "${DEST_KUBECONFIG}"
    else
      echo "❌ Error: File not found: ${KUBECONFIG_SOURCE}" >&2
      exit 1
    fi
  elif [[ -f "./workshop-credentials/kubeconfig-${TARGET_NS}.yaml" ]]; then
    echo "▶ Using local credential: ./workshop-credentials/kubeconfig-${TARGET_NS}.yaml..."
    cp "./workshop-credentials/kubeconfig-${TARGET_NS}.yaml" "${DEST_KUBECONFIG}"
  elif [[ -f "./workshop-credentials/kubeconfig-${TEAM}.yaml" ]]; then
    echo "▶ Using local credential: ./workshop-credentials/kubeconfig-${TEAM}.yaml..."
    cp "./workshop-credentials/kubeconfig-${TEAM}.yaml" "${DEST_KUBECONFIG}"
  fi
  chmod 600 "${DEST_KUBECONFIG}" 2>/dev/null || true
fi

# ------------------------------------------------------------------------------
# 3. Update agent/config.yaml (namespace & API key) and .env
# ------------------------------------------------------------------------------
CONFIG_FILE="${REPO_ROOT}/agent/config.yaml"
if [[ -f "$CONFIG_FILE" ]]; then
  echo "▶ Configuring namespace and LLM credentials in ${CONFIG_FILE}..."
  python3 -c "
import sys, re

config_path = sys.argv[1]
namespace = sys.argv[2]
api_key = sys.argv[3] if len(sys.argv) > 3 else ''

try:
    with open(config_path, 'r') as f:
        content = f.read()
    if namespace:
        content = re.sub(r'^(namespace:\s*)[\"\x27]?.*?[\"\x27]?(\s*(#.*)?)$', rf'\1\"{namespace}\"\2', content, flags=re.MULTILINE)
    if api_key:
        content = re.sub(r'^(api_key:\s*)[\"\x27]?.*?[\"\x27]?(\s*(#.*)?)$', rf'\1\"{api_key}\"\2', content, flags=re.MULTILINE)
    with open(config_path, 'w') as f:
        f.write(content)
except Exception as e:
    print(f'Warning: Could not update config.yaml: {e}', file=sys.stderr)
" "${CONFIG_FILE}" "${TARGET_NS}" "${API_KEY}"
  echo "✅ Target namespace ('${TARGET_NS}') and API key configured in ${CONFIG_FILE}"
fi

# Write local .env files for dotenv loaders and offline scripts
if [[ -n "$API_KEY" ]]; then
  for ENV_FILE in "${REPO_ROOT}/.env" "${REPO_ROOT}/agent/.env"; do
    if [[ -f "$ENV_FILE" ]]; then
      if grep -q "^OPENAI_API_KEY=" "$ENV_FILE"; then
        sed -i.bak -E "s/^OPENAI_API_KEY=\".*\"/OPENAI_API_KEY=\"${API_KEY}\"/" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
      else
        echo "OPENAI_API_KEY=\"${API_KEY}\"" >> "$ENV_FILE"
      fi
    else
      echo "OPENAI_API_KEY=\"${API_KEY}\"" > "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE" 2>/dev/null || true
  done
  echo "✅ LLM Gateway API key saved to .env"
fi

# ------------------------------------------------------------------------------
# 4. Configure OPENAI_API_KEY in Persistent Shell Profiles
# ------------------------------------------------------------------------------
if [[ -n "$API_KEY" ]]; then
  export OPENAI_API_KEY="$API_KEY"
  for RC in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.bash_profile" "${HOME}/.profile"; do
    if [[ -f "$RC" ]]; then
      if ! grep -q "OPENAI_API_KEY=" "$RC"; then
        echo "export OPENAI_API_KEY=\"${API_KEY}\"" >> "$RC"
      else
        sed -i.bak -E "s/export OPENAI_API_KEY=\".*\"/export OPENAI_API_KEY=\"${API_KEY}\"/" "$RC" && rm -f "${RC}.bak"
      fi
    fi
  done
  echo "✅ LLM Gateway API key configured in persistent shell profile(s)."
fi

# ------------------------------------------------------------------------------
# 5. Verify Cluster Connectivity
# ------------------------------------------------------------------------------
if [[ -f "${DEST_KUBECONFIG}" ]]; then
  SERVER_ENDPOINT=$(python3 -c "
import sys, re
try:
    with open(sys.argv[1], 'r') as f:
        for line in f:
            m = re.search(r'server:\s*[\"\x27]?(https?://[^\s\"\x27]+)', line)
            if m:
                print(m.group(1).rstrip('/'))
                break
except Exception:
    pass
" "${DEST_KUBECONFIG}")

  # 1. Instant check for RFC 1918 Private / Internal IPs
  if [[ "$SERVER_ENDPOINT" =~ https?://(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|127\.0\.0\.1|localhost) ]]; then
    echo ""
    echo "=========================================================================="
    echo "❌ CRITICAL CONFIGURATION ERROR: PRIVATE CLUSTER ENDPOINT"
    echo "=========================================================================="
    echo "Target Cluster Endpoint: ${SERVER_ENDPOINT}"
    echo ""
    echo "⚠️  DIAGNOSIS: The cluster server endpoint is an internal/private IP address."
    echo "   GitHub Codespaces cannot route traffic to internal private networks."
    echo "   The workshop bundle must be re-generated using the cluster's public endpoint."
    echo "=========================================================================="
    exit 1
  fi

  echo ""
  echo "▶ Testing live cluster connectivity (namespace: '${TARGET_NS}')..."

  # 2. Fast TCP / TLS probe (times out in 4s if port 443 is firewalled or unreachable)
  if command -v curl >/dev/null 2>&1 && [[ -n "${SERVER_ENDPOINT}" ]]; then
    if ! curl --connect-timeout 4 -k -sSL "${SERVER_ENDPOINT}/version" -o /dev/null 2>/dev/null; then
      echo ""
      echo "=========================================================================="
      echo "❌ CLUSTER CONNECTIVITY ERROR: ENDPOINT UNREACHABLE"
      echo "=========================================================================="
      echo "Target Cluster Endpoint: ${SERVER_ENDPOINT}"
      echo ""
      echo "⚠️  DIAGNOSIS: TCP connection to ${SERVER_ENDPOINT}:443 timed out or was refused."
      echo "   Possible causes:"
      echo "     1. GKE Authorized Networks does not allow access from this machine/Codespaces."
      echo "     2. Outbound firewall or proxy blocks port 443."
      echo "     3. The Kubernetes cluster control plane is offline."
      echo "=========================================================================="
      exit 1
    fi
  fi

  # 3. Authenticated Kubernetes API query
  if command -v kubectl >/dev/null 2>&1; then
    KUBE_ERR_FILE="${TMP_DIR}/kube_err.log"
    if KUBECONFIG="${DEST_KUBECONFIG}" kubectl get pods -n "${TARGET_NS}" --request-timeout='6s' > "${TMP_DIR}/pods.txt" 2> "${KUBE_ERR_FILE}"; then
      echo "✅ Cluster connectivity verified! Active pods in '${TARGET_NS}':"
      head -n 6 "${TMP_DIR}/pods.txt" || true
    else
      KUBE_ERR=$(cat "${KUBE_ERR_FILE}" 2>/dev/null || echo "Unknown error")
      echo ""
      echo "=========================================================================="
      echo "❌ KUBERNETES AUTHORIZATION / NAMESPACE ERROR"
      echo "=========================================================================="
      echo "Target Cluster Endpoint: ${SERVER_ENDPOINT}"
      echo "Error Details:"
      echo "  ${KUBE_ERR}"
      echo "=========================================================================="
      exit 1
    fi
  fi
fi

echo ""
echo "=========================================================================="
echo "🎉 TEAM ${TEAM} (NAMESPACE: ${TARGET_NS}) SETUP COMPLETE!"
echo "=========================================================================="
if [[ -n "$API_KEY" ]]; then
  echo "Credentials configured:"
  echo "  • LLM Gateway Key  : saved in agent/config.yaml & .env"
  echo "  • Current Shell Tip: To use \$OPENAI_API_KEY directly in this specific terminal without opening a new tab, run:"
  echo "                       export OPENAI_API_KEY=\"${API_KEY}\""
  echo ""
fi
echo "Next Steps:"
echo "  1. Verify your diagnostic tools: python3 agent/mini_agent.py --test-tools"
echo "  2. Run your first simulation:    python3 agent/mini_agent.py --scenario badDeploy1405 --model mock"
echo "  3. Run live with frontier model: python3 agent/mini_agent.py --scenario badDeploy1405"
echo "=========================================================================="
