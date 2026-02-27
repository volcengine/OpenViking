#!/bin/bash
# Vikingbot 镜像上传到火山引擎脚本
# 将 deploy/docker/deploy.sh 产生的本地镜像上传到火山引擎镜像仓库
# 用法: ./deploy/docker/image_upload.sh
# 变量: IMAGE_NAME, IMAGE_TAG, CONFIG_FILE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="${CONFIG_FILE:-${HOME}/.config/vikingbot/image_upload.yaml}"

# ── 颜色输出 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}$*${NC}"; }
log_ok()    { echo -e "${GREEN}$*${NC}"; }
log_warn()  { echo -e "${YELLOW}$*${NC}"; }
log_error() { echo -e "${RED}$*${NC}" >&2; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Upload local vikingbot image to Volcengine Container Registry.

Options:
  --config, -c FILE   Config file (default: ~/.config/vikingbot/image_upload.yaml)
  --image, -i NAME    Local image name (default: vikingbot)
  --tag, -t TAG       Image tag (default: latest)
  --help, -h          Show this help
EOF
}

# ── 参数解析 ─────────────────────────────────────────────────────────────────
# 先从配置文件读取默认值，然后命令行参数覆盖
IMAGE_NAME=""
IMAGE_TAG=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --config|-c)   CONFIG_FILE="$2"; shift 2 ;;
        --image|-i)    IMAGE_NAME="$2"; shift 2 ;;
        --tag|-t)      IMAGE_TAG="$2"; shift 2 ;;
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
    echo ""
    echo "Create one from the example:"
    echo "  mkdir -p \"$(dirname "$CONFIG_FILE")\""
    echo "  cp \"${PROJECT_ROOT}/deploy/docker/image_upload.example.yaml\" \"${CONFIG_FILE}\""
    exit 1
fi

# ── 安全读取 YAML 配置 ────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    log_error "python3 is required to parse the config file"
    exit 1
fi

TEMP_ENV=$(mktemp /tmp/vikingbot-upload-env.XXXXXX)
trap 'rm -f "$TEMP_ENV"' EXIT

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

# ── 默认值和参数覆盖 ──────────────────────────────────────────────────────────
# 从配置文件读取默认值，命令行参数优先
image_registry="${image_registry:-vikingbot-cn-beijing.cr.volces.com}"
image_namespace="${image_namespace:-vikingbot}"
image_repository="${image_repository:-vikingbot}"
use_timestamp_tag="${use_timestamp_tag:-false}"

# 本地镜像：命令行参数 > 配置文件 > 默认值
if [[ -z "$IMAGE_NAME" ]]; then
    IMAGE_NAME="${local_image_name:-vikingbot}"
fi
if [[ -z "$IMAGE_TAG" ]]; then
    IMAGE_TAG="${local_image_tag:-latest}"
fi

# 远程镜像标签
if [[ "$use_timestamp_tag" == "true" ]]; then
    REMOTE_IMAGE_TAG="build-$(date +%Y%m%d-%H%M%S)"
else
    REMOTE_IMAGE_TAG="${image_tag:-latest}"
fi

LOCAL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
FULL_REMOTE_IMAGE="${image_registry}/${image_namespace}/${image_repository}:${REMOTE_IMAGE_TAG}"

# ── 摘要 ──────────────────────────────────────────────────────────────────────
log_info "=================================================="
log_info "  Volcengine Image Upload Tool"
log_info "=================================================="
cat <<EOF
Config:        ${CONFIG_FILE}
Local image:   ${LOCAL_IMAGE}
Remote image:  ${FULL_REMOTE_IMAGE}
Registry:      ${image_registry}
EOF
echo ""

# ════════════════════════════════════════════════════════════════════════
# 步骤 1：检查本地镜像是否存在
# ════════════════════════════════════════════════════════════════════════
log_info "=== Step 1: Check local image ==="
if ! docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^${LOCAL_IMAGE}$"; then
    log_error "Local image not found: ${LOCAL_IMAGE}"
    echo ""
    echo "Please build the image first using:"
    echo "  ./deploy/docker/build-image.sh"
    echo "or"
    echo "  ./deploy/docker/deploy.sh"
    exit 1
fi
log_ok "Local image found: ${LOCAL_IMAGE}"

# ════════════════════════════════════════════════════════════════════════
# 步骤 2：登录到火山引擎镜像仓库
# ════════════════════════════════════════════════════════════════════════
log_info "=== Step 2: Login to registry ==="
if [[ -n "${registry_username:-}" && -n "${registry_password:-}" ]]; then
    echo "Logging in to ${image_registry} as ${registry_username}..."
    if ! printf '%s' "$registry_password" \
            | docker login "$image_registry" -u "$registry_username" --password-stdin; then
        log_error "Registry login failed"
        exit 1
    fi
    log_ok "Login success"
else
    log_warn "No registry credentials found in config"
    log_warn "Assuming already logged in or credentials are in docker config"
fi

# ════════════════════════════════════════════════════════════════════════
# 步骤 3：标记镜像
# ════════════════════════════════════════════════════════════════════════
log_info "=== Step 3: Tag image ==="
echo "Tagging: ${LOCAL_IMAGE} → ${FULL_REMOTE_IMAGE}"
if ! docker tag "$LOCAL_IMAGE" "$FULL_REMOTE_IMAGE"; then
    log_error "docker tag failed"
    exit 1
fi
log_ok "Tag success"

# ════════════════════════════════════════════════════════════════════════
# 步骤 4：推送镜像
# ════════════════════════════════════════════════════════════════════════
log_info "=== Step 4: Push image ==="
echo "Pushing: ${FULL_REMOTE_IMAGE}"
if ! docker push "$FULL_REMOTE_IMAGE"; then
    log_error "docker push failed"
    exit 1
fi
log_ok "Push success: ${FULL_REMOTE_IMAGE}"

echo ""
log_ok "All done!"
echo ""
echo "Useful commands:"
echo "  Pull:    ${YELLOW}docker pull ${FULL_REMOTE_IMAGE}${NC}"
echo "  Inspect: ${YELLOW}docker manifest inspect ${FULL_REMOTE_IMAGE}${NC}"
