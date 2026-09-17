#!/usr/bin/env bash
# ==============================================================================
# NudgeBee SRE & AIOps Workshop · Team Account & Kubeconfig Generator
# ==============================================================================
# Provisions isolated namespaces, RBAC ServiceAccounts, Prometheus proxy roles,
# and standalone kubeconfig files for workshop teams (group-1 to group-N).
#
# Usage:
#   ./scripts/generate-workshop-teams.sh [OPTIONS]
#
# Options:
#   --num-teams <N>           Number of teams to provision (default: 10)
#   --prefix <NAME>           Namespace prefix (default: group) -> group-1..group-N
#   --output-dir <DIR>        Output directory for kubeconfig files (default: ./workshop-credentials)
#   --duration <DUR>          Token validity duration (default: 48h)
#   --proxy-ns <NS>           Namespace where Prometheus service lives (default: nudgebee-agent)
#   --prom-svc <SVC>          Prometheus service name (default: nudgebee-prometheus-kube-p-prometheus)
#   --pass <PASS>             Room passphrase for AES-256 encrypted bundles (team-N.enc)
#   --api-key <KEY>           LLM Gateway / OpenAI API key to bundle into team payloads
#   --api-keys-file <FILE>    Path to file containing 1 API key per line for each team
#   --storage-url <URL>       Public Cloud Storage base URL for team downloads
#   --clean                   Teardown all created workshop team namespaces & RBAC
#   -h, --help                Show this help message
# ==============================================================================

set -euo pipefail

NUM_TEAMS=10
PREFIX="group"
OUTPUT_DIR="./workshop-credentials"
DURATION="48h"
PROXY_NS="nudgebee-agent"
PROM_SVC="nudgebee-prometheus-kube-p-prometheus"
CLEAN_MODE=false
PASSPHRASE=""
API_KEY=""
API_KEYS_FILE=""
STORAGE_BASE_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --num-teams)
      NUM_TEAMS="$2"
      shift 2
      ;;
    --prefix)
      PREFIX="$2"
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
    --api-keys-file)
      API_KEYS_FILE="$2"
      shift 2
      ;;
    --storage-url)
      STORAGE_BASE_URL="${2%/}"
      shift 2
      ;;
    --clean)
      CLEAN_MODE=true
      shift
      ;;
    -h|--help)
      sed -n '2,20p' "$0" | cut -c 3-
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# ------------------------------------------------------------------------------
# Pre-Flight Checks
# ------------------------------------------------------------------------------
if ! command -v kubectl >/dev/null 2>&1; then
  echo "❌ Error: 'kubectl' binary not found in PATH." >&2
  exit 1
fi

if ! kubectl cluster-info >/dev/null 2>&1; then
  echo "❌ Error: Unable to communicate with Kubernetes cluster. Check active kubeconfig." >&2
  exit 1
fi

CURRENT_CONTEXT=$(kubectl config current-context)
CLUSTER_NAME=$(kubectl config view -o jsonpath="{.contexts[?(@.name == \"${CURRENT_CONTEXT}\")].context.cluster}")
SERVER_URL=$(kubectl config view -o jsonpath="{.clusters[?(@.name == \"${CLUSTER_NAME}\")].cluster.server}")
CA_DATA=$(kubectl config view --raw -o jsonpath="{.clusters[?(@.name == \"${CLUSTER_NAME}\")].cluster.certificate-authority-data}")

# If CA_DATA is empty, check for insecure-skip-tls-verify
INSECURE_SKIP=$(kubectl config view -o jsonpath="{.clusters[?(@.name == \"${CLUSTER_NAME}\")].cluster.insecure-skip-tls-verify}" || echo "")

# ------------------------------------------------------------------------------
# Teardown Mode
# ------------------------------------------------------------------------------
if [[ "$CLEAN_MODE" == "true" ]]; then
  echo "⚠️  [TEARDOWN] Deleting ${NUM_TEAMS} workshop namespaces and RBAC..."
  for i in $(seq 1 "$NUM_TEAMS"); do
    NS="${PREFIX}-${i}"
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
echo "  └── Proxy Namespace   : ${PROXY_NS}"
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
echo "------------------------------------------------------------------------" >> "${SUMMARY_FILE}"

# ------------------------------------------------------------------------------
# Team Namespace & ServiceAccount Loop
# ------------------------------------------------------------------------------
for i in $(seq 1 "$NUM_TEAMS"); do
  TEAM_NS="${PREFIX}-${i}"
  SA_NAME="sa-${TEAM_NS}"
  KUBECONFIG_OUT="${OUTPUT_DIR}/kubeconfig-${TEAM_NS}.yaml"

  echo ""
  echo "▶ [Team ${i}/${NUM_TEAMS}] Provisioning namespace '${TEAM_NS}'..."

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
  resources: ["pods", "pods/log", "events", "services", "endpoints", "configmaps"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets", "statefulsets"]
  verbs: ["get", "list", "watch"]
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

  # 8. Resolve API Key for this Team
  ASSIGNED_KEY="${API_KEY}"
  if [[ -n "${API_KEYS_FILE}" && -f "${API_KEYS_FILE}" ]]; then
    ASSIGNED_KEY=$(sed -n "${i}p" "${API_KEYS_FILE}" || echo "")
  fi

  # 9. Optionally Generate AES-256 Encrypted Bundle
  if [[ -n "${PASSPHRASE}" ]]; then
    BUNDLE_OUT="${OUTPUT_DIR}/team-${i}.enc"
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
" "${TEAM_NS}" "${TEAM_NS}" "${ASSIGNED_KEY}" "${KUBECONFIG_CONTENT}" "${OUTPUT_DIR}/.tmp_team_${i}.json"

    openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 \
      -in "${OUTPUT_DIR}/.tmp_team_${i}.json" \
      -out "${BUNDLE_OUT}" \
      -pass pass:"${PASSPHRASE}"
    rm -f "${OUTPUT_DIR}/.tmp_team_${i}.json"

    chmod 644 "${BUNDLE_OUT}"
    echo "  🔒 Encrypted bundle created: ${BUNDLE_OUT}"
    echo "Team: ${TEAM_NS} | Kubeconfig: ${KUBECONFIG_OUT} | Encrypted Bundle: ${BUNDLE_OUT}" >> "${SUMMARY_FILE}"
    if [[ -n "${STORAGE_BASE_URL}" ]]; then
      echo "       Download: ${STORAGE_BASE_URL}/team-${i}.enc" >> "${SUMMARY_FILE}"
      echo "       Command : ./scripts/bootstrap-team.sh --team ${i} --pass \"${PASSPHRASE}\" --url \"${STORAGE_BASE_URL}\"" >> "${SUMMARY_FILE}"
    fi
  else
    echo "Team: ${TEAM_NS} | Kubeconfig: ${KUBECONFIG_OUT}" >> "${SUMMARY_FILE}"
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
  echo "   - Upload all *.enc files from '${OUTPUT_DIR}/' to your cloud bucket."
  echo "   - Attendees run: ./scripts/bootstrap-team.sh --team <N> --pass \"${PASSPHRASE}\""
fi
echo ""
echo "Quick Test Command:"
echo "  KUBECONFIG=${OUTPUT_DIR}/kubeconfig-${PREFIX}-1.yaml kubectl get pods"
echo "=========================================================================="
