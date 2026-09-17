#!/usr/bin/env bash
# ==============================================================================
# NudgeBee SRE & AIOps Workshop · Team Account & Kubeconfig Generator
# ==============================================================================
# Provisions isolated namespaces, RBAC ServiceAccounts, Prometheus proxy roles,
# and standalone kubeconfig files for workshop teams from a teams definition file.
#
# Usage:
#   ./scripts/generate-workshop-teams.sh --teams-file <FILE> [OPTIONS]
#
# Options:
#   --teams-file <FILE>       Path to file with: <team_id>,<namespace>,<api_key> (REQUIRED)
#   --output-dir <DIR>        Output directory for kubeconfig files (default: ./workshop-credentials)
#   --duration <DUR>          Token validity duration (default: 48h)
#   --proxy-ns <NS>           Namespace where Prometheus service lives (default: nudgebee-agent)
#   --prom-svc <SVC>          Prometheus service name (default: nudgebee-prometheus-kube-p-prometheus)
#   --pass <PASS>             Room passphrase for AES-256 encrypted bundles
#   --api-key <KEY>           Fallback LLM Gateway API key if not specified per-team
#   --storage-url <URL>       Public Cloud Storage base URL (e.g. https://storage.googleapis.com/<YOUR_BUCKET>)
#   --context <NAME>          Kubernetes context to target (default: active context)
#   --server-url <URL>        Override cluster server URL embedded in generated kubeconfigs
#   --clean                   Teardown workshop team namespaces & RBAC defined in teams file
#   --rbac-only               Re-apply namespace/SA/Role/RoleBinding only; leave tokens
#                             and encrypted bundles untouched (safe for live teams)
#   -h, --help                Show this help message
# ==============================================================================

set -euo pipefail

OUTPUT_DIR="./workshop-credentials"
DURATION="48h"
PROXY_NS="nudgebee-agent"
PROM_SVC="nudgebee-prometheus-kube-p-prometheus"
CLEAN_MODE=false
RBAC_ONLY=false
PASSPHRASE=""
API_KEY=""
TEAMS_FILE=""
STORAGE_BASE_URL=""
TARGET_CONTEXT=""
OVERRIDE_SERVER_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --teams-file)
      TEAMS_FILE="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --duration)
      DURATION="$2"
      shift 2
      ;;
    --proxy-ns)
      PROXY_NS="$2"
      shift 2
      ;;
    --prom-svc)
      PROM_SVC="$2"
      shift 2
      ;;
    --pass)
      PASSPHRASE="$2"
      shift 2
      ;;
    --api-key)
      API_KEY="$2"
      shift 2
      ;;
    --storage-url)
      STORAGE_BASE_URL="${2%/}"
      shift 2
      ;;
    --context)
      TARGET_CONTEXT="$2"
      shift 2
      ;;
    --server-url)
      OVERRIDE_SERVER_URL="$2"
      shift 2
      ;;
    --clean)
      CLEAN_MODE=true
      shift
      ;;
    --rbac-only)
      RBAC_ONLY=true
      shift
      ;;
    -h|--help)
      sed -n '2,24p' "$0" | cut -c 3-
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# ------------------------------------------------------------------------------
# Pre-Flight Checks & Context Resolution
# ------------------------------------------------------------------------------
if [[ -z "${TEAMS_FILE}" ]]; then
  echo "❌ Error: Missing required option --teams-file <FILE>." >&2
  echo "Usage: $0 --teams-file <FILE> [OPTIONS]" >&2
  exit 1
fi

if [[ ! -f "${TEAMS_FILE}" ]]; then
  echo "❌ Error: Teams file '${TEAMS_FILE}' not found." >&2
  exit 1
fi

# ------------------------------------------------------------------------------
# Resolve Teams, Namespaces, and API Keys
# ------------------------------------------------------------------------------
TEAM_IDS=()
NAMESPACES=()
KEYS=()

echo "▶ Loading teams from file: ${TEAMS_FILE}"
while IFS= read -r line || [[ -n "$line" ]]; do
  line=$(echo "$line" | sed 's/#.*//' | xargs)
  [[ -z "$line" ]] && continue
  if [[ "$line" == *","* ]]; then
    T_ID=$(echo "$line" | awk -F',' '{print $1}' | xargs)
    T_NS=$(echo "$line" | awk -F',' '{print $2}' | xargs)
    T_KEY=$(echo "$line" | awk -F',' '{print $3}' | xargs)
  else
    T_ID=$(echo "$line" | awk '{print $1}')
    T_NS=$(echo "$line" | awk '{print $2}')
    T_KEY=$(echo "$line" | awk '{print $3}')
  fi
  [[ -z "$T_NS" ]] && T_NS="${T_ID}"
  [[ -z "$T_KEY" ]] && T_KEY="${API_KEY}"

  TEAM_IDS+=("$T_ID")
  NAMESPACES+=("$T_NS")
  KEYS+=("$T_KEY")
done < "${TEAMS_FILE}"

if [[ ${#TEAM_IDS[@]} -eq 0 ]]; then
  echo "❌ Error: No valid team entries found in '${TEAMS_FILE}'." >&2
  exit 1
fi

NUM_TEAMS=${#TEAM_IDS[@]}
echo "  └── Loaded ${NUM_TEAMS} team(s) from ${TEAMS_FILE}"

# ------------------------------------------------------------------------------
# Kubernetes Context & Connectivity
# ------------------------------------------------------------------------------
if ! command -v kubectl >/dev/null 2>&1; then
  echo "❌ Error: 'kubectl' binary not found in PATH." >&2
  exit 1
fi

if [[ -n "${TARGET_CONTEXT}" ]]; then
  if ! kubectl config get-contexts "${TARGET_CONTEXT}" >/dev/null 2>&1; then
    echo "❌ Error: Context '${TARGET_CONTEXT}' not found in kubeconfig." >&2
    exit 1
  fi
  ORIGINAL_CONTEXT=$(kubectl config current-context)
  trap 'kubectl config use-context "${ORIGINAL_CONTEXT}" >/dev/null 2>&1 || true' EXIT
  echo "▶ Switching active context to: ${TARGET_CONTEXT}"
  kubectl config use-context "${TARGET_CONTEXT}" >/dev/null
  CURRENT_CONTEXT="${TARGET_CONTEXT}"
else
  CURRENT_CONTEXT=$(kubectl config current-context)
fi

if ! kubectl cluster-info >/dev/null 2>&1; then
  echo "❌ Error: Unable to communicate with Kubernetes cluster in context '${CURRENT_CONTEXT}'." >&2
  exit 1
fi

CLUSTER_NAME=$(kubectl config view -o jsonpath="{.contexts[?(@.name == \"${CURRENT_CONTEXT}\")].context.cluster}")
SERVER_URL=$(kubectl config view -o jsonpath="{.clusters[?(@.name == \"${CLUSTER_NAME}\")].cluster.server}")
if [[ -n "${OVERRIDE_SERVER_URL}" ]]; then
  SERVER_URL="${OVERRIDE_SERVER_URL}"
fi

CA_DATA=$(kubectl config view --raw -o jsonpath="{.clusters[?(@.name == \"${CLUSTER_NAME}\")].cluster.certificate-authority-data}")
INSECURE_SKIP=$(kubectl config view -o jsonpath="{.clusters[?(@.name == \"${CLUSTER_NAME}\")].cluster.insecure-skip-tls-verify}" || echo "")

# Safety Warning for RFC 1918 Private IP Endpoints
if [[ "$SERVER_URL" =~ https?://(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|127\.0\.0\.1|localhost) ]]; then
  echo ""
  echo "=========================================================================="
  echo "⚠️  CRITICAL WARNING: TARGET CLUSTER ENDPOINT IS A PRIVATE / INTERNAL IP!"
  echo "=========================================================================="
  echo "Active Context : ${CURRENT_CONTEXT}"
  echo "Server URL     : ${SERVER_URL}"
  echo ""
  echo "Attendees in GitHub Codespaces will NOT be able to connect to this private IP"
  echo "and will experience connection timeouts."
  echo ""
  echo "Recommended Actions:"
  echo "  1. Target the public workshop cluster context:"
  echo "     --context <PUBLIC_CONTEXT_NAME>"
  echo "  2. Or override with a public endpoint URL:"
  echo "     --server-url https://<PUBLIC_IP>"
  echo "=========================================================================="
  echo ""
  read -r -p "? Are you sure you want to generate bundles with this private IP? (y/N): " CONFIRM_PRIVATE
  if [[ ! "$CONFIRM_PRIVATE" =~ ^[Yy]$ ]]; then
    echo "Aborted by user."
    exit 1
  fi
fi

# ------------------------------------------------------------------------------
# Teardown Mode
# ------------------------------------------------------------------------------
if [[ "$CLEAN_MODE" == "true" ]]; then
  echo "⚠️  [TEARDOWN] Deleting ${NUM_TEAMS} workshop namespaces and RBAC..."
  for NS in "${NAMESPACES[@]}"; do
    echo "  - Deleting namespace: ${NS}"
    kubectl delete namespace "${NS}" --ignore-not-found=true --wait=false || true
    kubectl delete rolebinding "workshop-prom-proxy-${NS}" -n "${PROXY_NS}" --ignore-not-found=true || true
  done
  kubectl delete role "workshop-prom-proxy" -n "${PROXY_NS}" --ignore-not-found=true || true
  echo "✅ Teardown initiated for all workshop namespaces."
  exit 0
fi

# ------------------------------------------------------------------------------
# Setup Prometheus Proxy Role in Proxy Namespace
# ------------------------------------------------------------------------------
echo "=========================================================================="
echo "🐝 PROVISIONING NUDGEBEE WORKSHOP TEAMS (${NUM_TEAMS} TEAMS)"
echo "=========================================================================="
echo "  ├── Kubernetes Server : ${SERVER_URL}"
echo "  ├── Current Context   : ${CURRENT_CONTEXT}"
echo "  ├── Output Directory  : ${OUTPUT_DIR}"
echo "  ├── Token Duration    : ${DURATION}"
echo "  ├── Proxy Namespace   : ${PROXY_NS}"
if [[ -n "${STORAGE_BASE_URL}" ]]; then
  echo "  └── Storage Base URL  : ${STORAGE_BASE_URL}"
else
  echo "  └── Storage Base URL  : (none specified)"
fi
echo "=========================================================================="

mkdir -p "${OUTPUT_DIR}"

# Ensure proxy namespace role exists for Prometheus queries via API server
if kubectl get namespace "${PROXY_NS}" >/dev/null 2>&1; then
  echo "▶ Setting up Prometheus proxy RBAC in namespace '${PROXY_NS}'..."
  cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: workshop-prom-proxy
  namespace: ${PROXY_NS}
rules:
- apiGroups: [""]
  resources: ["services/proxy"]
  verbs: ["get"]
EOF
else
  echo "⚠️ Warning: Proxy namespace '${PROXY_NS}' not found on cluster. Prometheus proxy role skipped."
fi

SUMMARY_FILE="${OUTPUT_DIR}/credentials-summary.txt"
echo "# NudgeBee SRE Workshop Team Credentials" > "${SUMMARY_FILE}"
echo "# Generated at: $(date -u '+%Y-%m-%d %H:%M:%SZ')" >> "${SUMMARY_FILE}"
echo "# Cluster Endpoint: ${SERVER_URL}" >> "${SUMMARY_FILE}"
if [[ -n "${STORAGE_BASE_URL}" ]]; then
  echo "# Storage Base URL: ${STORAGE_BASE_URL}" >> "${SUMMARY_FILE}"
fi
echo "------------------------------------------------------------------------" >> "${SUMMARY_FILE}"

# ------------------------------------------------------------------------------
# Team Namespace & ServiceAccount Loop
# ------------------------------------------------------------------------------
for idx in "${!TEAM_IDS[@]}"; do
  i=$((idx + 1))
  TEAM_ID="${TEAM_IDS[$idx]}"
  TEAM_NS="${NAMESPACES[$idx]}"
  ASSIGNED_KEY="${KEYS[$idx]}"
  SA_NAME="sa-${TEAM_NS}"
  KUBECONFIG_OUT="${OUTPUT_DIR}/kubeconfig-${TEAM_NS}.yaml"
  BUNDLE_OUT="${OUTPUT_DIR}/${TEAM_ID}.enc"

  echo ""
  echo "▶ [Team ${i}/${NUM_TEAMS}] Provisioning team '${TEAM_ID}' (Namespace: '${TEAM_NS}')..."

  # 1. Create Namespace
  kubectl create namespace "${TEAM_NS}" --dry-run=client -o yaml | kubectl apply -f -

  # 2. Create Team ServiceAccount
  cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${SA_NAME}
  namespace: ${TEAM_NS}
EOF

  # 3. Create Scoped Role in Team Namespace
  cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: workshop-agent-role
  namespace: ${TEAM_NS}
rules:
# Diagnostic read permissions for Agent
- apiGroups: [""]
  resources: ["pods", "pods/log", "events", "services", "endpoints"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets", "statefulsets"]
  verbs: ["get", "list", "watch"]
# Scenario fault injection: mini_agent.py --scenario patches the team's own
# flagd-config ConfigMap to enable the fault, then restarts flagd. Without
# "patch" the agent would investigate a healthy cluster and be graded as failing.
# Role is namespace-scoped, so a team can only ever break its own stack.
- apiGroups: [""]
  resources: ["configmaps"]
  verbs: ["get", "list", "watch", "patch", "update"]
# Remediating permissions (for human-authorized rollout undo / patch)
- apiGroups: ["apps"]
  resources: ["deployments", "deployments/rollback", "deployments/scale"]
  verbs: ["get", "patch", "update"]
EOF

  # 4. Bind Role to ServiceAccount
  cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: workshop-agent-binding
  namespace: ${TEAM_NS}
subjects:
- kind: ServiceAccount
  name: ${SA_NAME}
  namespace: ${TEAM_NS}
roleRef:
  kind: Role
  name: workshop-agent-role
  apiGroup: rbac.authorization.k8s.io
EOF

  # 5. Bind Prometheus Proxy Role in Proxy Namespace (if it exists)
  if kubectl get namespace "${PROXY_NS}" >/dev/null 2>&1; then
    cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: workshop-prom-proxy-${TEAM_NS}
  namespace: ${PROXY_NS}
subjects:
- kind: ServiceAccount
  name: ${SA_NAME}
  namespace: ${TEAM_NS}
roleRef:
  kind: Role
  name: workshop-prom-proxy
  apiGroup: rbac.authorization.k8s.io
EOF
  fi

  # Re-applying RBAC to existing teams must not mint new tokens or rebuild
  # bundles - that would invalidate credentials already handed to attendees.
  if [[ "${RBAC_ONLY}" == "true" ]]; then
    echo "  RBAC applied for ${TEAM_NS} (token, kubeconfig and bundle left untouched)."
    continue
  fi

  # 6. Generate ServiceAccount Bearer Token
  TOKEN=""
  if TOKEN=$(kubectl create token "${SA_NAME}" -n "${TEAM_NS}" --duration="${DURATION}" 2>/dev/null); then
    :
  else
    # Fallback for clusters without TokenRequest API: create secret-based token
    SECRET_NAME="${SA_NAME}-token"
    cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: ${SECRET_NAME}
  namespace: ${TEAM_NS}
  annotations:
    kubernetes.io/service-account.name: ${SA_NAME}
type: kubernetes.io/service-account-token
EOF
    sleep 1
    TOKEN=$(kubectl get secret "${SECRET_NAME}" -n "${TEAM_NS}" -o jsonpath='{.data.token}' | base64 --decode)
  fi

  # 7. Assemble Standalone Kubeconfig
  if [[ -n "${CA_DATA}" ]]; then
    cat > "${KUBECONFIG_OUT}" <<EOF
apiVersion: v1
kind: Config
preferences: {}
current-context: ${TEAM_NS}
clusters:
- cluster:
    certificate-authority-data: ${CA_DATA}
    server: ${SERVER_URL}
  name: workshop-cluster
contexts:
- context:
    cluster: workshop-cluster
    namespace: ${TEAM_NS}
    user: ${SA_NAME}
  name: ${TEAM_NS}
users:
- name: ${SA_NAME}
  user:
    token: ${TOKEN}
EOF
  else
    cat > "${KUBECONFIG_OUT}" <<EOF
apiVersion: v1
kind: Config
preferences: {}
current-context: ${TEAM_NS}
clusters:
- cluster:
    insecure-skip-tls-verify: true
    server: ${SERVER_URL}
  name: workshop-cluster
contexts:
- context:
    cluster: workshop-cluster
    namespace: ${TEAM_NS}
    user: ${SA_NAME}
  name: ${TEAM_NS}
users:
- name: ${SA_NAME}
  user:
    token: ${TOKEN}
EOF
  fi

  chmod 600 "${KUBECONFIG_OUT}"
  echo "  ✅ Kubeconfig generated: ${KUBECONFIG_OUT}"

  # 8. Optionally Generate AES-256 Encrypted Bundle
  if [[ -n "${PASSPHRASE}" ]]; then
    KUBECONFIG_CONTENT=$(cat "${KUBECONFIG_OUT}")

    python3 -c "
import json, sys
data = {
    'team': sys.argv[1],
    'namespace': sys.argv[2],
    'api_key': sys.argv[3],
    'kubeconfig': sys.argv[4]
}
with open(sys.argv[5], 'w') as f:
    json.dump(data, f)
" "${TEAM_ID}" "${TEAM_NS}" "${ASSIGNED_KEY}" "${KUBECONFIG_CONTENT}" "${OUTPUT_DIR}/.tmp_${TEAM_ID}.json"

    openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 \
      -in "${OUTPUT_DIR}/.tmp_${TEAM_ID}.json" \
      -out "${BUNDLE_OUT}" \
      -pass pass:"${PASSPHRASE}"
    rm -f "${OUTPUT_DIR}/.tmp_${TEAM_ID}.json"

    chmod 644 "${BUNDLE_OUT}"
    echo "  🔒 Encrypted bundle created: ${BUNDLE_OUT}"
    echo "Team: ${TEAM_ID} | Namespace: ${TEAM_NS} | Encrypted Bundle: ${BUNDLE_OUT}" >> "${SUMMARY_FILE}"
    if [[ -n "${STORAGE_BASE_URL}" ]]; then
      echo "       Download: ${STORAGE_BASE_URL}/${TEAM_ID}.enc" >> "${SUMMARY_FILE}"
      echo "       Command : ./scripts/bootstrap-team.sh --team ${TEAM_ID} --pass \"${PASSPHRASE}\" --url \"${STORAGE_BASE_URL}\"" >> "${SUMMARY_FILE}"
    else
      echo "       Command : ./scripts/bootstrap-team.sh --team ${TEAM_ID} --pass \"${PASSPHRASE}\" --url \"<STORAGE_URL>\"" >> "${SUMMARY_FILE}"
    fi
  else
    echo "Team: ${TEAM_ID} | Namespace: ${TEAM_NS} | Kubeconfig: ${KUBECONFIG_OUT}" >> "${SUMMARY_FILE}"
  fi
done

echo ""
echo "=========================================================================="
echo "🎉 ALL ${NUM_TEAMS} WORKSHOP TEAMS PROVISIONED SUCCESSFULLY!"
echo "=========================================================================="
echo "Credential files saved to: ${OUTPUT_DIR}/"
if [[ -n "${PASSPHRASE}" ]]; then
  echo ""
  echo "🔒 Encrypted bundles ready for Cloud Storage distribution:"
  echo "   Upload Command (e.g. Google Cloud Storage, Cloudflare R2, AWS S3):"
  echo "     gcloud storage cp ${OUTPUT_DIR}/*.enc gs://<YOUR_BUCKET>/"
  echo "     # or: aws s3 cp ${OUTPUT_DIR}/ s3://<YOUR_BUCKET>/ --recursive --exclude \"*\" --include \"*.enc\""
  echo ""
  echo "   Attendee Command:"
  if [[ -n "${STORAGE_BASE_URL}" ]]; then
    echo "     ./scripts/bootstrap-team.sh --team <TEAM_ID> --pass \"${PASSPHRASE}\" --url \"${STORAGE_BASE_URL}\""
  else
    echo "     ./scripts/bootstrap-team.sh --team <TEAM_ID> --pass \"${PASSPHRASE}\" --url \"<STORAGE_URL>\""
  fi
fi
echo ""
echo "Quick Test Command:"
echo "  KUBECONFIG=${OUTPUT_DIR}/kubeconfig-${NAMESPACES[0]}.yaml kubectl get pods"
echo "=========================================================================="
