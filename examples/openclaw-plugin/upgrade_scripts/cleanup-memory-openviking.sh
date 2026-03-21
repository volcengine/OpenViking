#!/bin/bash
#
# 从旧版 memory-openviking 插件升级到新版 openviking 的前置清理脚本
#
# 用法：
#   bash cleanup-legacy-openviking.sh
#   bash cleanup-legacy-openviking.sh --workdir ~/.openclaw-second  # 指定 OpenClaw 目录
#
set -euo pipefail

# --- 参数解析 ---
OPENCLAW_DIR="${HOME}/.openclaw"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --workdir) OPENCLAW_DIR="$2"; shift 2 ;;
        -h|--help)
            echo "用法：$0 [--workdir <openclaw目录>]"
            exit 0 ;;
        *) echo "未知参数：$1"; exit 1 ;;
    esac
done

CONFIG_FILE="${OPENCLAW_DIR}/openclaw.json"
LEGACY_PLUGIN_ID="memory-openviking"
BAK_SUFFIX=".pre-openviking-upgrade.bak"

# --- 颜色输出 ---
info()  { printf '\033[0;32m[INFO] \033[0m%s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN] \033[0m%s\n' "$*"; }
error() { printf '\033[0;31m[ERROR]\033[0m%s\n' "$*"; }

# --- 前置检查 ---
if ! command -v openclaw &>/dev/null; then
    error "未找到 openclaw 命令，请先安装 OpenClaw"
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
    if [ "${OPENCLAW_DIR}" = "${HOME}/.openclaw" ]; then
        error "请确认 OpenClaw 已安装并初始化（openclaw onboard）"
        error "如果使用了非默认安装路径，请通过 --workdir 参数指定："
        error "  bash $0 --workdir /your/openclaw/dir"
    else
        error "请确认该目录下存在有效的 OpenClaw 配置"
    fi
    exit 1
fi

info "OpenClaw 目录：${OPENCLAW_DIR}"
info "配置文件：${CONFIG_FILE}"
echo ""

# ============================================================
# Step 1: 停止 OpenClaw gateway
# ============================================================
info "Step 1: 停止 OpenClaw gateway..."
if openclaw gateway stop 2>/dev/null; then
    info "gateway 已停止"
else
    warn "gateway 可能未在运行，继续..."
fi
echo ""

# ============================================================
# Step 2: 备份配置文件和旧版插件目录
# ============================================================
info "Step 2: 备份旧版配置..."

cp "${CONFIG_FILE}" "${CONFIG_FILE}${BAK_SUFFIX}"
info "配置已备份至 ${CONFIG_FILE}${BAK_SUFFIX}"

LEGACY_PLUGIN_DIR="${OPENCLAW_DIR}/extensions/${LEGACY_PLUGIN_ID}"
if [ -d "${LEGACY_PLUGIN_DIR}" ]; then
    DISABLED_DIR="${OPENCLAW_DIR}/disabled-extensions"
    mkdir -p "${DISABLED_DIR}"
    mv "${LEGACY_PLUGIN_DIR}" "${DISABLED_DIR}/${LEGACY_PLUGIN_ID}-upgrade-backup"
    info "插件目录已移至 ${DISABLED_DIR}/${LEGACY_PLUGIN_ID}-upgrade-backup"
else
    warn "未找到旧版插件目录 ${LEGACY_PLUGIN_DIR}，跳过"
fi
echo ""

# ============================================================
# Step 3: 清理 openclaw.json 中的旧版插件配置
#
# 注意：openclaw config 命令无法处理带连字符的 key（如 memory-openviking），
# 也不支持按值删除数组元素，因此全部改用 Node.js 直接操作 JSON 文件，
# 确保解析正确、不破坏 JSON 结构。Node.js 为 OpenClaw 运行时依赖，必定可用。
# ============================================================
info "Step 3: 清理 openclaw.json 中的旧版插件配置..."

if ! command -v node &>/dev/null; then
    error "未找到 node 命令，无法自动清理配置"
    error "请手动编辑 ${CONFIG_FILE}，完成以下操作："
    error "  1. 从 plugins.allow 中删除 \"${LEGACY_PLUGIN_ID}\""
    error "  2. 从 plugins.load.paths 中删除包含 ${LEGACY_PLUGIN_ID} 的路径"
    error "  3. 删除 plugins.entries.${LEGACY_PLUGIN_ID} 整个对象"
    error "  4. 将 plugins.slots.memory 改为 \"none\""
    exit 1
fi

node -e "
const fs = require('fs');
const file = process.argv[1];
const pluginId = process.argv[2];

let cfg;
try {
  cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
} catch (e) {
  console.error('JSON 解析失败：' + e.message);
  process.exit(1);
}

const plugins = cfg.plugins;
if (!plugins) {
  console.log('未找到 plugins 字段，跳过');
  process.exit(0);
}

// 从 allow 数组移除旧插件 ID
if (Array.isArray(plugins.allow)) {
  plugins.allow = plugins.allow.filter(x => x !== pluginId);
}

// 从 load.paths 移除包含旧插件 ID 的路径
if (Array.isArray(plugins.load?.paths)) {
  plugins.load.paths = plugins.load.paths.filter(x => !x.includes(pluginId));
}

// 删除 entries[pluginId]（整个对象）
if (plugins.entries && pluginId in plugins.entries) {
  delete plugins.entries[pluginId];
}

// 将 slots.memory 改为 'none'（仅当其值为旧插件 ID 时）
if (plugins.slots?.memory === pluginId) {
  plugins.slots.memory = 'none';
}

fs.writeFileSync(file, JSON.stringify(cfg, null, 2) + '\n');
" "${CONFIG_FILE}" "${LEGACY_PLUGIN_ID}"

info "已从 plugins.allow 中移除 \"${LEGACY_PLUGIN_ID}\""
info "已从 plugins.load.paths 中移除旧版路径"
info "已删除 plugins.entries.${LEGACY_PLUGIN_ID}"
info "已将 plugins.slots.memory 设为 none"
echo ""

# ============================================================
# 完成
# ============================================================
info "✓ 前置清理完成"
info ""
info "下一步：安装新版 openviking 插件"
info "  npm install -g openclaw-openviking-setup-helper && ov-install"
info ""
info "如需回滚，恢复备份："
info "  openclaw gateway stop"
info "  cp ${CONFIG_FILE}${BAK_SUFFIX} ${CONFIG_FILE}"
info "  mv ${OPENCLAW_DIR}/disabled-extensions/${LEGACY_PLUGIN_ID}-upgrade-backup \\"
info "     ${OPENCLAW_DIR}/extensions/${LEGACY_PLUGIN_ID}"
