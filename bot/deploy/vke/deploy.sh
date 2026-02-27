#!/bin/bash
# Vikingbot VKE 一键部署脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="${HOME}/.config/vikingbot/vke_deploy.yaml"
SKIP_BUILD=false
SKIP_PUSH=false
SKIP_DEPLOY=false
NO_CACHE=false

# ── 颜色输出 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}$*${NC}"; }
log_ok()    { echo -e "${GREEN}$*${NC}"; }
log_error() { echo -e "${RED}$*${NC}" >&2; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --skip-build        Skip Docker image build
  --skip-push         Skip Docker image push
  --skip-deploy       Skip Kubernetes deploy
  --no-cache          Force rebuild without Docker layer cache
  --config, -c FILE   Config file (default: ~/.config/vikingbot/vke_deploy.yaml)
  --help, -h          Show this help
EOF
}

# ── 参数解析 ─────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-build)  SKIP_BUILD=true;  shift ;;
        --skip-push)   SKIP_PUSH=true;   shift ;;
        --skip-deploy) SKIP_DEPLOY=true; shift ;;
        --no-cache)    NO_CACHE=true;    shift ;;
        --config|-c)   CONFIG_FILE="$2"; shift 2 ;;
        --help|-h)     usage; exit 0 ;;
        *)
            log_error "Unknown argument: $1"
            usage >&2
            exit 1
            ;;
    esac
done

# ── 配置文件检查 ──────────────────────────────────────────────────────────────
if [[ ! -f "$CONFIG_FILE" ]]; then
    log_error "Config file not found: ${CONFIG_FILE}"
    echo "Create one from the example:"
    echo "  mkdir -p \"$(dirname "$CONFIG_FILE")\""
    echo "  cp \"${SCRIPT_DIR}/vke_deploy.example.yaml\" \"${CONFIG_FILE}\""
    exit 1
fi

# ── 安全读取 YAML 配置 ────────────────────────────────────────────────────────
# 用 Python 解析 YAML 后以 shlex.quote 安全转义输出，再 source 到当前 shell，
# 避免原版 eval + 未转义字符串带来的注入风险，同时正确处理整数/布尔值
if ! command -v python3 &>/dev/null; then
    log_error "python3 is required to parse the config file"
    exit 1
fi

TEMP_ENV=$(mktemp /tmp/vikingbot-env.XXXXXX)
TEMP_MANIFEST=$(mktemp /tmp/vikingbot-manifest.XXXXXX)
trap 'rm -f "$TEMP_ENV" "$TEMP_MANIFEST"' EXIT

python3 - "$CONFIG_FILE" >"$TEMP_ENV" <<'PYEOF'
import sys, shlex

config_path = sys.argv[1]
config = {}

try:
    import yaml
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
except ImportError:
    # Fallback: 无 pyyaml 时的简单解析
    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or ':' not in line:
                continue
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                config[key] = val

for key, val in config.items():
    if not key.isidentifier():
        continue
    if isinstance(val, bool):
        str_val = 'true' if val else 'false'
    elif val is None:
        str_val = ''
    else:
        str_val = str(val)
    print(f"{key}={shlex.quote(str_val)}")
PYEOF

# shellcheck source=/dev/null
source "$TEMP_ENV"

# ── 校验必要字段 ──────────────────────────────────────────────────────────────
# 只拒绝明确的占位符值，不误伤真实 AK（Volcengine 真实 AK 本身就以 AKLT 开头）
PLACEHOLDERS=("AKLTxxxxxxxxxx" "xxxxxxxxxx" "ccxxxxxxxxxx")
missing=()
for field in volcengine_access_key volcengine_secret_key vke_cluster_id; do
    val="${!field:-}"
    rejected=false
    if [[ -z "$val" ]]; then
        rejected=true
    else
        for ph in "${PLACEHOLDERS[@]}"; do
            [[ "$val" == "$ph" ]] && rejected=true && break
        done
    fi
    $rejected && missing+=("$field")
done

if [[ ${#missing[@]} -gt 0 ]]; then
    log_error "Config validation failed! Missing or placeholder fields:"
    for f in "${missing[@]}"; do log_error "  - $f"; done
    echo ""
    echo "Edit: ${CONFIG_FILE}"
    exit 1
fi

# ── 默认值 ────────────────────────────────────────────────────────────────────
image_registry="${image_registry:-vikingbot-cn-beijing.cr.volces.com}"
image_namespace="${image_namespace:-vikingbot}"
image_repository="${image_repository:-vikingbot}"
image_tag="${image_tag:-latest}"
local_image_name="${local_image_name:-vikingbot-vke}"
dockerfile_path="${dockerfile_path:-deploy/Dockerfile}"
build_context="${build_context:-.}"
k8s_namespace="${k8s_namespace:-default}"
k8s_deployment_name="${k8s_deployment_name:-vikingbot}"
k8s_replicas="${k8s_replicas:-1}"
k8s_manifest_path="${k8s_manifest_path:-deploy/vke/k8s/deployment.yaml}"
kubeconfig_path="${kubeconfig_path:-}"
storage_type="${storage_type:-local}"
tos_bucket="${tos_bucket:-vikingbot_data}"
tos_path="${tos_path:-/.vikingbot/}"
tos_region="${tos_region:-cn-beijing}"
use_timestamp_tag="${use_timestamp_tag:-false}"
wait_for_rollout="${wait_for_rollout:-true}"
rollout_timeout="${rollout_timeout:-120}"

# 时间戳 tag（原版有展示但未实现，此处补全）
if [[ "$use_timestamp_tag" == "true" ]]; then
    image_tag="build-$(date +%Y%m%d-%H%M%S)"
fi

# 相对路径 → 基于 PROJECT_ROOT 的绝对路径（原版未使用已定义的 PROJECT_ROOT）
_abs() { [[ "$1" == /* ]] && echo "$1" || echo "${PROJECT_ROOT}/$1"; }
dockerfile_path=$(_abs "$dockerfile_path")
k8s_manifest_path=$(_abs "$k8s_manifest_path")
if [[ "$build_context" == "." ]]; then
    build_context="$PROJECT_ROOT"
else
    build_context=$(_abs "$build_context")
fi

# kubeconfig（原版完全未处理此配置项）
if [[ -n "$kubeconfig_path" ]]; then
    export KUBECONFIG="${kubeconfig_path/#\~/$HOME}"
fi

full_image_name="${image_registry}/${image_namespace}/${image_repository}:${image_tag}"

# ── 摘要 ──────────────────────────────────────────────────────────────────────
log_info "=================================================="
log_info "  Volcengine VKE One-Click Deployment Tool"
log_info "=================================================="
cat <<EOF
Config:        ${CONFIG_FILE}
Region:        ${volcengine_region:-cn-beijing}
Cluster ID:    ${vke_cluster_id}
Image:         ${full_image_name}
Timestamp tag: ${use_timestamp_tag}
Dockerfile:    ${dockerfile_path}
K8s manifest:  ${k8s_manifest_path}
Storage type:  ${storage_type}
EOF
echo ""

# ════════════════════════════════════════════════════════════════════════
# 步骤 1：构建 Docker 镜像
# ════════════════════════════════════════════════════════════════════════
if [[ "$SKIP_BUILD" == false ]]; then
    log_info "=== Step 1: Build Docker image ==="

    if [[ ! -f "$dockerfile_path" ]]; then
        log_error "Dockerfile not found: ${dockerfile_path}"
        exit 1
    fi

    build_args=(docker build --platform linux/amd64 -f "$dockerfile_path" -t "$local_image_name")
    [[ "$NO_CACHE" == true ]] && build_args+=(--no-cache)
    build_args+=("$build_context")
    echo "${build_args[*]}"
    if ! "${build_args[@]}"; then
        log_error "Build image failed"
        exit 1
    fi
    log_ok "Image build success: ${local_image_name}"
else
    log_info "=== Step 1: Skip image build ==="
fi

# ════════════════════════════════════════════════════════════════════════
# 步骤 2：推送镜像到仓库
# ════════════════════════════════════════════════════════════════════════
if [[ "$SKIP_PUSH" == false ]]; then
    log_info "=== Step 2: Push image to registry ==="

    if [[ -n "${registry_username:-}" && -n "${registry_password:-}" ]]; then
        echo "Logging in to ${image_registry} as ${registry_username}..."
        # --password-stdin 避免密码出现在进程列表（原版 -p 存在此安全问题）
        if ! printf '%s' "$registry_password" \
                | docker login "$image_registry" -u "$registry_username" --password-stdin; then
            log_error "Registry login failed"
            exit 1
        fi
    fi

    echo "Tagging: ${local_image_name} → ${full_image_name}"
    if ! docker tag "$local_image_name" "$full_image_name"; then
        log_error "docker tag failed"
        exit 1
    fi

    echo "Pushing: ${full_image_name}"
    if ! docker push "$full_image_name"; then
        log_error "docker push failed"
        exit 1
    fi
    log_ok "Image push success: ${full_image_name}"
else
    log_info "=== Step 2: Skip image push ==="
fi

# ════════════════════════════════════════════════════════════════════════
# 步骤 3：部署到 Kubernetes
# ════════════════════════════════════════════════════════════════════════
if [[ "$SKIP_DEPLOY" == false ]]; then
    log_info "=== Step 3: Deploy to Kubernetes ==="

    if [[ ! -f "$k8s_manifest_path" ]]; then
        log_error "K8s manifest not found: ${k8s_manifest_path}"
        exit 1
    fi

    manifest=$(cat "$k8s_manifest_path")
    manifest="${manifest//__IMAGE_NAME__/$full_image_name}"
    echo "Image    → ${full_image_name}"
    manifest="${manifest//__REPLICAS__/$k8s_replicas}"
    echo "Replicas → ${k8s_replicas}"

    # ── 存储配置 ──────────────────────────────────────────────────────────
    case "$storage_type" in
        tos)
            # base64 无换行（Linux 默认换行，| tr -d '\n' 统一抹掉，兼容两端）
            ak_b64=$(printf '%s' "$volcengine_access_key" | base64 | tr -d '\n')
            sk_b64=$(printf '%s' "$volcengine_secret_key" | base64 | tr -d '\n')

            prepend="apiVersion: v1
kind: Secret
metadata:
  name: vikingbot-tos-secret
  namespace: ${k8s_namespace}
type: Opaque
data:
  AccessKeyId: ${ak_b64}
  SecretAccessKey: ${sk_b64}
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: vikingbot-tos-pv
spec:
  capacity:
    storage: 10Gi
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: \"\"
  csi:
    driver: fsx.csi.volcengine.com
    volumeHandle: vikingbot-tos-pv
    volumeAttributes:
      bucket: ${tos_bucket}
      path: ${tos_path}
      subpath: /
      type: TOS
      region: ${tos_region}
      server: tos-${tos_region}.ivolces.com
      secretName: vikingbot-tos-secret
      secretNamespace: ${k8s_namespace}
---"
            manifest="${prepend}
${manifest}"
            manifest="${manifest//__ACCESS_MODES__/ReadWriteMany}"
            manifest="${manifest//__STORAGE_CLASS_CONFIG__/}"
            manifest="${manifest//__VOLUME_NAME_CONFIG__/volumeName: vikingbot-tos-pv}"
            echo "Storage  → TOS (bucket=${tos_bucket}, region=${tos_region})"
            ;;

        local|*)
            manifest="${manifest//__ACCESS_MODES__/ReadWriteOnce}"
            manifest="${manifest//__STORAGE_CLASS_CONFIG__/storageClassName: csi-ebs-ssd-default}"
            manifest="${manifest//__VOLUME_NAME_CONFIG__/}"
            echo "Storage  → EBS (local)"
            ;;
    esac

    printf '%s\n' "$manifest" > "$TEMP_MANIFEST"

    echo "Applying manifest to namespace: ${k8s_namespace}..."
    if ! kubectl apply -f "$TEMP_MANIFEST" -n "$k8s_namespace"; then
        log_error "kubectl apply failed"
        exit 1
    fi
    log_ok "K8s resources applied"

    if [[ "$wait_for_rollout" == "true" ]]; then
        echo ""
        echo "Waiting for rollout (timeout: ${rollout_timeout}s)..."
        if ! kubectl rollout status "deployment/${k8s_deployment_name}" \
                -n "$k8s_namespace" --timeout="${rollout_timeout}s"; then
            log_error "Rollout timeout or failed"
            echo "Diagnose: kubectl get pods -n ${k8s_namespace}"
            echo "Logs:     kubectl logs -l app=vikingbot -n ${k8s_namespace} --tail=50"
            exit 1
        fi
        log_ok "Deployment success!"
    fi
else
    log_info "=== Step 3: Skip K8s deploy ==="
fi

echo ""
log_ok "All done!"
