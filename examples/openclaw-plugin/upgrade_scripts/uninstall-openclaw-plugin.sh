#!/bin/bash
#
# 卸载新版 openviking 插件的脚本
#
# 用法：
#   bash uninstall-openviking.sh
#   bash uninstall-openviking.sh --workdir ~/.openclaw-dir  # 指定 OpenClaw 目录
#
set -euo pipefail

info()  { printf '\033[0;32m[INFO] \033[0m%s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN] \033[0m%s\n' "$*"; }
error() { printf '\033[0;31m[ERROR]\033[0m%s\n' "$*"; }

HOME_DIR="${HOME}"
OPENCLAW_DIR="${HOME_DIR}/.openclaw"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workdir)
            if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == -* ]]; then
                error "--workdir 需要提供一个目录路径"
                echo "用法：$0 [--workdir <openclaw目录>]"
                exit 1
            fi
            OPENCLAW_DIR="$2"
            shift 2 ;;
        -h|--help)
            echo "用法：$0 [--workdir <openclaw目录>]"
            exit 0 ;;
        *)
            error "未知参数：$1"
            exit 1 ;;
    esac
done

CONFIG_FILE="${OPENCLAW_DIR}/openclaw.json"
PLUGIN_ID="openviking"
PLUGIN_DIR="${OPENCLAW_DIR}/extensions/${PLUGIN_ID}"
DISABLED_DIR="${OPENCLAW_DIR}/disabled-extensions"
PLUGIN_BACKUP_DIR="${DISABLED_DIR}/${PLUGIN_ID}-uninstall-backup-$(date +%Y%m%d%H%M%S)"
BAK_SUFFIX=".pre-openviking-uninstall.bak"

CONFIG_BACKUP_CREATED=0
PLUGIN_MOVED=0

rollback_on_error() {
    local exit_code="$?"
    trap - ERR

    error "卸载过程中发生错误，正在尝试回滚..."

    if [ "${CONFIG_BACKUP_CREATED}" -eq 1 ] && [ -f "${CONFIG_FILE}${BAK_SUFFIX}" ]; then
        cp "${CONFIG_FILE}${BAK_SUFFIX}" "${CONFIG_FILE}" && warn "已恢复配置文件 ${CONFIG_FILE}"
    fi

    if [ "${PLUGIN_MOVED}" -eq 1 ] && [ -d "${PLUGIN_BACKUP_DIR}" ] && [ ! -d "${PLUGIN_DIR}" ]; then
        mkdir -p "$(dirname "${PLUGIN_DIR}")"
        mv "${PLUGIN_BACKUP_DIR}" "${PLUGIN_DIR}" && warn "已恢复插件目录 ${PLUGIN_DIR}"
    fi

    exit "${exit_code}"
}

trap 'rollback_on_error' ERR

if ! command -v openclaw &>/dev/null; then
    error "未找到 openclaw 命令，请先安装 OpenClaw"
    exit 1
fi

if ! command -v node &>/dev/null; then
    error "未找到 node 命令，无法自动清理 OpenClaw 配置"
    exit 1
fi

if [ ! -d "${OPENCLAW_DIR}" ]; then
    error "未找到 OpenClaw 目录：${OPENCLAW_DIR}"
    error "如果使用了非默认安装路径，请通过 --workdir 参数指定："
    error "  bash $0 --workdir /your/openclaw/dir"
    exit 1
fi

if [ ! -f "${CONFIG_FILE}" ]; then
    error "未找到 OpenClaw 配置文件：${CONFIG_FILE}"
    if [ "${OPENCLAW_DIR}" = "${HOME_DIR}/.openclaw" ]; then
        error "请确认 OpenClaw 已安装并初始化（openclaw onboard）"
        error "如果使用了非默认安装路径，请通过 --workdir 参数指定："
        error "  bash $0 --workdir /your/openclaw/dir"
    else
        error "请确认该目录下存在有效的 OpenClaw 配置"
    fi
    exit 1
fi

run_openclaw() {
    if [ "${OPENCLAW_DIR}" != "${HOME_DIR}/.openclaw" ]; then
        OPENCLAW_STATE_DIR="${OPENCLAW_DIR}" openclaw "$@"
    else
        openclaw "$@"
    fi
}

info "OpenClaw 目录：${OPENCLAW_DIR}"
info "配置文件：${CONFIG_FILE}"
info "OpenViking 服务/运行时：保留，不做卸载"
echo ""

# ============================================================
# Step 1: 停止 OpenClaw gateway
# ============================================================
info "Step 1: 停止 OpenClaw gateway..."
if run_openclaw gateway stop 2>/dev/null; then
    info "gateway 已停止"
else
    warn "gateway 可能未在运行，继续..."
fi
echo ""

# ============================================================
# Step 2: 备份当前配置
# ============================================================
info "Step 2: 备份当前配置..."
cp "${CONFIG_FILE}" "${CONFIG_FILE}${BAK_SUFFIX}"
CONFIG_BACKUP_CREATED=1
info "配置已备份至 ${CONFIG_FILE}${BAK_SUFFIX}"
echo ""

# ============================================================
# Step 3: 清理 openclaw.json 中的新版插件配置
# ============================================================
info "Step 3: 清理 openclaw.json 中的新版插件配置..."

CONFIG_STATUS="$(
node - "${CONFIG_FILE}" "${PLUGIN_ID}" <<'NODE'
const fs = require('fs');

const file = process.argv[2];
const pluginId = process.argv[3];
const pluginPathPattern = new RegExp(pluginId, 'i');
const status = {
  plugins: 'missing',
  allow: 'skipped',
  loadPaths: 'skipped',
  entry: 'skipped',
  slot: 'skipped',
};

let cfg;
try {
  cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
} catch (e) {
  console.error('JSON 解析失败：' + e.message);
  process.exit(1);
}

const plugins = cfg.plugins;
if (!plugins) {
  console.log(`plugins=${status.plugins}`);
  console.log(`allow=${status.allow}`);
  console.log(`loadPaths=${status.loadPaths}`);
  console.log(`entry=${status.entry}`);
  console.log(`slot=${status.slot}`);
  process.exit(0);
}

status.plugins = 'present';

if (Array.isArray(plugins.allow)) {
  const nextAllow = plugins.allow.filter((x) => x !== pluginId);
  status.allow = nextAllow.length !== plugins.allow.length ? 'removed' : 'unchanged';
  plugins.allow = nextAllow;
} else {
  status.allow = 'unchanged';
}

if (Array.isArray(plugins.load?.paths)) {
  const nextPaths = plugins.load.paths.filter((x) => typeof x !== 'string' || !pluginPathPattern.test(x));
  status.loadPaths = nextPaths.length !== plugins.load.paths.length ? 'removed' : 'unchanged';
  plugins.load.paths = nextPaths;
} else {
  status.loadPaths = 'unchanged';
}

if (plugins.entries && pluginId in plugins.entries) {
  delete plugins.entries[pluginId];
  status.entry = 'removed';
} else {
  status.entry = 'unchanged';
}

if (plugins.slots?.contextEngine === pluginId) {
  plugins.slots.contextEngine = 'legacy';
  status.slot = 'rolled_back';
} else {
  status.slot = 'unchanged';
}

fs.writeFileSync(file, JSON.stringify(cfg, null, 2) + '\n');
console.log(`plugins=${status.plugins}`);
console.log(`allow=${status.allow}`);
console.log(`loadPaths=${status.loadPaths}`);
console.log(`entry=${status.entry}`);
console.log(`slot=${status.slot}`);
NODE
)"

PLUGINS_STATUS=""
ALLOW_STATUS=""
LOAD_PATHS_STATUS=""
ENTRY_STATUS=""
SLOT_STATUS=""
while IFS='=' read -r key value; do
    case "${key}" in
        plugins) PLUGINS_STATUS="${value}" ;;
        allow) ALLOW_STATUS="${value}" ;;
        loadPaths) LOAD_PATHS_STATUS="${value}" ;;
        entry) ENTRY_STATUS="${value}" ;;
        slot) SLOT_STATUS="${value}" ;;
    esac
done <<< "${CONFIG_STATUS}"

if [ "${PLUGINS_STATUS}" = "missing" ]; then
    warn "openclaw.json 中未找到 plugins 字段，未修改插件配置"
else
    if [ "${ALLOW_STATUS}" = "removed" ]; then
        info "已从 plugins.allow 中移除 \"${PLUGIN_ID}\""
    else
        info "plugins.allow 中未找到 \"${PLUGIN_ID}\"，跳过"
    fi

    if [ "${LOAD_PATHS_STATUS}" = "removed" ]; then
        info "已从 plugins.load.paths 中移除包含 ${PLUGIN_ID} 的路径"
    else
        info "plugins.load.paths 中未找到包含 ${PLUGIN_ID} 的路径，跳过"
    fi

    if [ "${ENTRY_STATUS}" = "removed" ]; then
        info "已删除 plugins.entries.${PLUGIN_ID}"
    else
        info "plugins.entries.${PLUGIN_ID} 不存在，跳过"
    fi

    if [ "${SLOT_STATUS}" = "rolled_back" ]; then
        info "已将 plugins.slots.contextEngine 回退为 legacy"
    else
        info "plugins.slots.contextEngine 当前不是 ${PLUGIN_ID}，保持不变"
    fi
fi
echo ""

# ============================================================
# Step 4: 恢复 OpenClaw memory 为 memory-core
# ============================================================
info "Step 4: 恢复 OpenClaw memory 为 memory-core..."
run_openclaw plugins enable memory-core >/dev/null
info "已执行：openclaw plugins enable memory-core"
echo ""

# ============================================================
# Step 5: 备份插件目录
# ============================================================
info "Step 5: 备份插件目录..."
if [ -d "${PLUGIN_DIR}" ]; then
    mkdir -p "${DISABLED_DIR}"
    mv "${PLUGIN_DIR}" "${PLUGIN_BACKUP_DIR}"
    PLUGIN_MOVED=1
    info "插件目录已移至 ${PLUGIN_BACKUP_DIR}"
else
    warn "未找到插件目录 ${PLUGIN_DIR}，跳过目录备份"
fi
echo ""

# ============================================================
# Step 6: 保留 OpenViking 服务/运行时
# ============================================================
info "Step 6: 保留 OpenViking 服务/运行时..."
info "已保留 Python 包 openviking、~/.openviking 目录及 openviking.env 环境文件"
echo ""

# ============================================================
# 完成
# ============================================================
info "✓ 卸载完成"
info ""
info "请重启 OpenClaw 继续使用："
info "  服务模式：openclaw gateway start"
info "  已在运行的服务：openclaw gateway restart"
info "  前台直接运行：openclaw gateway"
info ""
info "如需恢复插件配置："
info "  1. openclaw gateway stop"
info "  2. cp ${CONFIG_FILE}${BAK_SUFFIX} ${CONFIG_FILE}"
if [ -d "${PLUGIN_BACKUP_DIR}" ]; then
    info "  3. mv ${PLUGIN_BACKUP_DIR} ${PLUGIN_DIR}"
fi
info ""
info "如需重新安装新版 openviking 插件："
info "  npm install -g openclaw-openviking-setup-helper && ov-install"

trap - ERR
