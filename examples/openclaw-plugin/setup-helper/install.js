#!/usr/bin/env node
/**
 * OpenClaw + OpenViking cross-platform installer
 *
 * One-liner (after npm publish; use package name + bin name):
 *   npx -p openclaw-openviking-setup-helper ov-install [ -y ] [ --zh ] [ --workdir PATH ]
 * Or install globally then run:
 *   npm i -g openclaw-openviking-setup-helper
 *   ov-install
 *   openclaw-openviking-install
 *
 * Direct run:
 *   node install.js [ -y | --yes ] [ --zh ] [ --workdir PATH ] [ --upgrade-plugin ]
 *                   [ --plugin-version=TAG ] [ --openviking-version=V ] [ --repo=PATH ]
 *
 * Environment variables:
 *   REPO, PLUGIN_VERSION (or BRANCH), OPENVIKING_INSTALL_YES, SKIP_OPENCLAW, SKIP_OPENVIKING
 *   OPENVIKING_VERSION       Pip install openviking==VERSION (omit for latest)
 *   OPENVIKING_REPO          Repo path: source install (pip -e) + local plugin (default: off)
 *   NPM_REGISTRY, PIP_INDEX_URL
 *   OPENVIKING_VLM_API_KEY, OPENVIKING_EMBEDDING_API_KEY, OPENVIKING_ARK_API_KEY
 *   OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES (Linux)
 */

import { spawn } from "node:child_process";
import { cp, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { existsSync, readdirSync } from "node:fs";
import { basename, dirname, join, relative } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

let REPO = process.env.REPO || "volcengine/OpenViking";
// PLUGIN_VERSION takes precedence over BRANCH (legacy)
let PLUGIN_VERSION = process.env.PLUGIN_VERSION || process.env.BRANCH || "main";
const NPM_REGISTRY = process.env.NPM_REGISTRY || "https://registry.npmmirror.com";
const PIP_INDEX_URL = process.env.PIP_INDEX_URL || "https://mirrors.volces.com/pypi/simple/";

const IS_WIN = process.platform === "win32";
const HOME = process.env.HOME || process.env.USERPROFILE || "";

const DEFAULT_OPENCLAW_DIR = join(HOME, ".openclaw");
let OPENCLAW_DIR = DEFAULT_OPENCLAW_DIR;
let PLUGIN_DEST = "";  // Will be set after resolving plugin config

const OPENVIKING_DIR = join(HOME, ".openviking");

const DEFAULT_SERVER_PORT = 1933;
const DEFAULT_AGFS_PORT = 1833;
const DEFAULT_VLM_MODEL = "doubao-seed-2-0-pro-260215";
const DEFAULT_EMBED_MODEL = "doubao-embedding-vision-251215";

// Fallback configs for old versions without manifest
const FALLBACK_LEGACY = {
  dir: "openclaw-memory-plugin",
  id: "memory-openviking",
  kind: "memory",
  slot: "memory",
  required: ["index.ts", "config.ts", "openclaw.plugin.json", "package.json"],
  optional: ["package-lock.json", ".gitignore"],
};

// Must match examples/openclaw-plugin/install-manifest.json (npm only installs package deps, not these .ts files).
const FALLBACK_CURRENT = {
  dir: "openclaw-plugin",
  id: "openviking",
  kind: "context-engine",
  slot: "contextEngine",
  required: ["index.ts", "config.ts", "package.json"],
  optional: [
    "context-engine.ts",
    "client.ts",
    "process-manager.ts",
    "memory-ranking.ts",
    "text-utils.ts",
    "tool-call-id.ts",
    "session-transcript-repair.ts",
    "openclaw.plugin.json",
    "tsconfig.json",
    "package-lock.json",
    ".gitignore",
  ],
};

const PLUGIN_VARIANTS = [
  { ...FALLBACK_LEGACY, generation: "legacy", slotFallback: "none" },
  { ...FALLBACK_CURRENT, generation: "current", slotFallback: "legacy" },
];

// Resolved plugin config (set by resolvePluginConfig)
let resolvedPluginDir = "";
let resolvedPluginId = "";
let resolvedPluginKind = "";
let resolvedPluginSlot = "";
let resolvedFilesRequired = [];
let resolvedFilesOptional = [];
let resolvedNpmOmitDev = true;
let resolvedMinOpenclawVersion = "";
let resolvedMinOpenvikingVersion = "";
let resolvedPluginReleaseId = "";

let installYes = process.env.OPENVIKING_INSTALL_YES === "1";
let langZh = false;
let openvikingVersion = process.env.OPENVIKING_VERSION || "";
let openvikingRepo = process.env.OPENVIKING_REPO || "";
let workdirExplicit = false;
let upgradePluginOnly = false;
let rollbackLastUpgrade = false;

let selectedMode = "local";
let selectedServerPort = DEFAULT_SERVER_PORT;
let remoteBaseUrl = "http://127.0.0.1:1933";
let remoteApiKey = "";
let remoteAgentId = "";
let openvikingPythonPath = "";
let upgradeRuntimeConfig = null;
let installedUpgradeState = null;
let upgradeAudit = null;

const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  const arg = argv[i];
  if (arg === "-y" || arg === "--yes") {
    installYes = true;
    continue;
  }
  if (arg === "--zh") {
    langZh = true;
    continue;
  }
  if (arg === "--upgrade-plugin" || arg === "--update" || arg === "--upgrade") {
    upgradePluginOnly = true;
    continue;
  }
  if (arg === "--rollback" || arg === "--rollback-last-upgrade") {
    rollbackLastUpgrade = true;
    continue;
  }
  if (arg === "--workdir") {
    const workdir = argv[i + 1]?.trim();
    if (!workdir) {
      console.error("--workdir requires a path");
      process.exit(1);
    }
    setOpenClawDir(workdir);
    workdirExplicit = true;
    i += 1;
    continue;
  }
  if (arg.startsWith("--plugin-version=")) {
    PLUGIN_VERSION = arg.slice("--plugin-version=".length).trim();
    continue;
  }
  if (arg === "--plugin-version") {
    const version = argv[i + 1]?.trim();
    if (!version) {
      console.error("--plugin-version requires a value");
      process.exit(1);
    }
    PLUGIN_VERSION = version;
    i += 1;
    continue;
  }
  if (arg.startsWith("--openviking-version=")) {
    openvikingVersion = arg.slice("--openviking-version=".length).trim();
    continue;
  }
  if (arg === "--openviking-version") {
    const version = argv[i + 1]?.trim();
    if (!version) {
      console.error("--openviking-version requires a value");
      process.exit(1);
    }
    openvikingVersion = version;
    i += 1;
    continue;
  }
  if (arg.startsWith("--repo=")) {
    openvikingRepo = arg.slice("--repo=".length).trim();
    continue;
  }
  if (arg.startsWith("--github-repo=")) {
    REPO = arg.slice("--github-repo=".length).trim();
    continue;
  }
  if (arg === "--github-repo") {
    const repo = argv[i + 1]?.trim();
    if (!repo) {
      console.error("--github-repo requires a value (e.g. owner/repo)");
      process.exit(1);
    }
    REPO = repo;
    i += 1;
    continue;
  }
  if (arg === "-h" || arg === "--help") {
    printHelp();
    process.exit(0);
  }
}

function setOpenClawDir(dir) {
  OPENCLAW_DIR = dir;
}

function printHelp() {
  console.log("Usage: node install.js [ OPTIONS ]");
  console.log("");
  console.log("Options:");
  console.log("  --github-repo=OWNER/REPO GitHub repository (default: volcengine/OpenViking)");
  console.log("  --plugin-version=TAG     Plugin version (Git tag, e.g. v0.2.9, default: main)");
  console.log("  --openviking-version=V   OpenViking PyPI version (e.g. 0.2.9, default: latest)");
  console.log("  --workdir PATH           OpenClaw config directory (default: ~/.openclaw)");
  console.log("  --repo=PATH              Use local OpenViking repo at PATH (pip -e + local plugin)");
  console.log("  --update, --upgrade-plugin");
  console.log("                           Upgrade only the plugin to the requested --plugin-version; keep ov.conf and do not change the OpenViking service");
  console.log("  --rollback, --rollback-last-upgrade");
  console.log("                           Roll back the last plugin upgrade using the saved audit/backup files");
  console.log("  -y, --yes                Non-interactive (use defaults)");
  console.log("  --zh                     Chinese prompts");
  console.log("  -h, --help               This help");
  console.log("");
  console.log("Examples:");
  console.log("  # Install latest version");
  console.log("  node install.js");
  console.log("");
  console.log("  # Install from a fork repository");
  console.log("  node install.js --github-repo=yourname/OpenViking --plugin-version=dev-branch");
  console.log("");
  console.log("  # Install specific plugin version");
  console.log("  node install.js --plugin-version=v0.2.8");
  console.log("");
  console.log("  # Upgrade only the plugin files");
  console.log("  node install.js --update --plugin-version=main");
  console.log("");
  console.log("  # Roll back the last plugin upgrade");
  console.log("  node install.js --rollback");
  console.log("");
  console.log("Env: REPO, PLUGIN_VERSION, OPENVIKING_VERSION, SKIP_OPENCLAW, SKIP_OPENVIKING, NPM_REGISTRY, PIP_INDEX_URL");
}

function formatCliArg(value) {
  if (!value) {
    return "";
  }
  return /[\s"]/u.test(value) ? JSON.stringify(value) : value;
}

function getLegacyInstallCommandHint() {
  const override = process.env.OPENVIKING_INSTALL_LEGACY_HINT?.trim();
  if (override) {
    return override;
  }

  const invokedScript = process.argv[1] ? basename(process.argv[1]) : "";
  const args = ["--plugin-version", "<legacy-version>"];
  if (workdirExplicit || OPENCLAW_DIR !== DEFAULT_OPENCLAW_DIR) {
    args.push("--workdir", formatCliArg(OPENCLAW_DIR));
  }
  if (REPO !== "volcengine/OpenViking") {
    args.push("--github-repo", formatCliArg(REPO));
  }
  if (langZh) {
    args.push("--zh");
  }

  if (invokedScript === "install.js") {
    return `node install.js ${args.join(" ")}`;
  }

  return `ov-install ${args.join(" ")}`;
}

function tr(en, zh) {
  return langZh ? zh : en;
}

function info(msg) {
  console.log(`[INFO] ${msg}`);
}

function warn(msg) {
  console.log(`[WARN] ${msg}`);
}

function err(msg) {
  console.log(`[ERROR] ${msg}`);
}

function bold(msg) {
  console.log(msg);
}

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      stdio: opts.silent ? "pipe" : "inherit",
      shell: opts.shell ?? true,
      ...opts,
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`exit ${code}`));
    });
  });
}

function runCapture(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, {
      stdio: ["ignore", "pipe", "pipe"],
      shell: opts.shell ?? false,
      ...opts,
    });
    let out = "";
    let errOut = "";
    child.stdout?.on("data", (chunk) => {
      out += String(chunk);
    });
    child.stderr?.on("data", (chunk) => {
      errOut += String(chunk);
    });
    child.on("error", (error) => {
      resolve({ code: -1, out: "", err: String(error) });
    });
    child.on("close", (code) => {
      resolve({ code, out: out.trim(), err: errOut.trim() });
    });
  });
}

function runLiveCapture(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, {
      stdio: ["ignore", "pipe", "pipe"],
      shell: opts.shell ?? false,
      ...opts,
    });
    let out = "";
    let errOut = "";
    child.stdout?.on("data", (chunk) => {
      const text = String(chunk);
      out += text;
      process.stdout.write(text);
    });
    child.stderr?.on("data", (chunk) => {
      const text = String(chunk);
      errOut += text;
      process.stderr.write(text);
    });
    child.on("error", (error) => {
      resolve({ code: -1, out: "", err: String(error) });
    });
    child.on("close", (code) => {
      resolve({ code, out: out.trim(), err: errOut.trim() });
    });
  });
}

function question(prompt, defaultValue = "") {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const suffix = defaultValue ? ` [${defaultValue}]` : "";
  return new Promise((resolve) => {
    rl.question(`${prompt}${suffix}: `, (answer) => {
      rl.close();
      resolve((answer ?? defaultValue).trim() || defaultValue);
    });
  });
}

async function resolveAbsoluteCommand(cmd) {
  if (cmd.startsWith("/") || (IS_WIN && /^[A-Za-z]:[/\\]/.test(cmd))) return cmd;
  if (IS_WIN) {
    const r = await runCapture("where", [cmd], { shell: true });
    return r.out.split(/\r?\n/)[0]?.trim() || cmd;
  }
  const r = await runCapture("which", [cmd], { shell: false });
  return r.out.trim() || cmd;
}

async function checkPython() {
  const raw = process.env.OPENVIKING_PYTHON || (IS_WIN ? "python" : "python3");
  const py = await resolveAbsoluteCommand(raw);
  const result = await runCapture(py, ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]);
  if (result.code !== 0 || !result.out) {
    return {
      ok: false,
      detail: tr("Python not found or failed. Install Python >= 3.10.", "Python 未找到或执行失败，请安装 Python >= 3.10"),
      cmd: py,
    };
  }
  const [major, minor] = result.out.split(".").map(Number);
  if (major < 3 || (major === 3 && minor < 10)) {
    return {
      ok: false,
      detail: tr(`Python ${result.out} is too old. Need >= 3.10.`, `Python ${result.out} 版本过低，需要 >= 3.10`),
      cmd: py,
    };
  }
  return { ok: true, detail: result.out, cmd: py };
}

async function checkNode() {
  const result = await runCapture("node", ["-v"], { shell: IS_WIN });
  if (result.code !== 0 || !result.out) {
    return { ok: false, detail: tr("Node.js not found. Install Node.js >= 22.", "Node.js 未找到，请安装 Node.js >= 22") };
  }
  const major = Number.parseInt(result.out.replace(/^v/, "").split(".")[0], 10);
  if (!Number.isFinite(major) || major < 22) {
    return { ok: false, detail: tr(`Node.js ${result.out} is too old. Need >= 22.`, `Node.js ${result.out} 版本过低，需要 >= 22`) };
  }
  return { ok: true, detail: result.out };
}

function detectOpenClawInstances() {
  const instances = [];
  try {
    const entries = readdirSync(HOME, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      if (entry.name === ".openclaw" || entry.name.startsWith(".openclaw-")) {
        instances.push(join(HOME, entry.name));
      }
    }
  } catch {}
  return instances.sort();
}

async function selectWorkdir() {
  if (workdirExplicit) return;

  const instances = detectOpenClawInstances();
  if (instances.length <= 1) return;
  if (installYes) return;

  console.log("");
  bold(tr("Found multiple OpenClaw instances:", "发现多个 OpenClaw 实例："));
  for (let i = 0; i < instances.length; i++) {
    console.log(`  ${i + 1}) ${instances[i]}`);
  }
  console.log("");

  const answer = await question(tr("Select instance number", "选择实例编号"), "1");
  const index = Number.parseInt(answer, 10) - 1;
  if (index >= 0 && index < instances.length) {
    setOpenClawDir(instances[index]);
  } else {
    warn(tr("Invalid selection, using default", "无效选择，使用默认"));
    setOpenClawDir(instances[0]);
  }
}

async function selectMode() {
  if (installYes) {
    selectedMode = "local";
    return;
  }
  const mode = (await question(tr("Plugin mode - local or remote", "插件模式 - local 或 remote"), "local")).toLowerCase();
  selectedMode = mode === "remote" ? "remote" : "local";
}

async function collectRemoteConfig() {
  if (installYes) return;
  remoteBaseUrl = await question(tr("OpenViking server URL", "OpenViking 服务器地址"), remoteBaseUrl);
  remoteApiKey = await question(tr("API Key (optional)", "API Key（可选）"), remoteApiKey);
  remoteAgentId = await question(tr("Agent ID (optional)", "Agent ID（可选）"), remoteAgentId);
}

async function validateEnvironment() {
  info(tr("Checking OpenViking runtime environment...", "正在校验 OpenViking 运行环境..."));
  console.log("");

  const missing = [];

  const python = await checkPython();
  if (python.ok) {
    info(`  Python: ${python.detail} ✓`);
  } else {
    missing.push(`Python 3.10+ | ${python.detail}`);
  }

  const node = await checkNode();
  if (node.ok) {
    info(`  Node.js: ${node.detail} ✓`);
  } else {
    missing.push(`Node.js 22+ | ${node.detail}`);
  }

  if (missing.length > 0) {
    console.log("");
    err(tr("Environment check failed. Install missing dependencies first.", "环境校验未通过，请先安装以下缺失组件。"));
    console.log("");
    if (missing.some((item) => item.startsWith("Python"))) {
      console.log(tr("Python (example):", "Python（示例）："));
      if (IS_WIN) console.log("  winget install --id Python.Python.3.11 -e");
      else console.log("  pyenv install 3.11.12 && pyenv global 3.11.12");
      console.log("");
    }
    if (missing.some((item) => item.startsWith("Node"))) {
      console.log(tr("Node.js (example):", "Node.js（示例）："));
      if (IS_WIN) console.log("  nvm install 22.22.0 && nvm use 22.22.0");
      else console.log("  nvm install 22 && nvm use 22");
      console.log("");
    }
    process.exit(1);
  }

  console.log("");
  info(tr("Environment check passed ✓", "环境校验通过 ✓"));
  console.log("");
}

async function checkOpenClaw() {
  if (process.env.SKIP_OPENCLAW === "1") {
    info(tr("Skipping OpenClaw check (SKIP_OPENCLAW=1)", "跳过 OpenClaw 校验 (SKIP_OPENCLAW=1)"));
    return;
  }

  info(tr("Checking OpenClaw...", "正在校验 OpenClaw..."));
  const result = await runCapture("openclaw", ["--version"], { shell: IS_WIN });
  if (result.code === 0) {
    info(tr("OpenClaw detected ✓", "OpenClaw 已安装 ✓"));
    return;
  }

  err(tr("OpenClaw not found. Install it manually, then rerun this script.", "未检测到 OpenClaw，请先手动安装后再执行本脚本"));
  console.log("");
  console.log(tr("Recommended command:", "推荐命令："));
  console.log(`  npm install -g openclaw --registry ${NPM_REGISTRY}`);
  console.log("");
  console.log("  openclaw --version");
  console.log("  openclaw onboard");
  console.log("");
  process.exit(1);
}

// Compare versions: returns true if v1 >= v2
function versionGte(v1, v2) {
  const parseVersion = (v) => {
    const cleaned = v.replace(/^v/, "").replace(/-.*$/, "");
    const parts = cleaned.split(".").map((p) => Number.parseInt(p, 10) || 0);
    while (parts.length < 3) parts.push(0);
    return parts;
  };
  const [a1, a2, a3] = parseVersion(v1);
  const [b1, b2, b3] = parseVersion(v2);
  if (a1 !== b1) return a1 > b1;
  if (a2 !== b2) return a2 > b2;
  return a3 >= b3;
}

function isSemverLike(value) {
  return /^v?\d+(\.\d+){1,2}$/.test(value);
}

function validateRequestedPluginVersion() {
  if (!isSemverLike(PLUGIN_VERSION)) return;
  if (versionGte(PLUGIN_VERSION, "v0.2.7") && !versionGte(PLUGIN_VERSION, "v0.2.8")) {
    err(tr("Plugin version v0.2.7 does not exist.", "插件版本 v0.2.7 不存在。"));
    process.exit(1);
  }
}

if (upgradePluginOnly && rollbackLastUpgrade) {
  console.error("--update/--upgrade-plugin and --rollback cannot be used together");
  process.exit(1);
}

function ensurePluginOnlyOperationArgs() {
  if ((upgradePluginOnly || rollbackLastUpgrade) && openvikingVersion) {
    err(
      tr(
        "Plugin-only upgrade/rollback does not support --openviking-version. Use --plugin-version to choose the plugin release, and run a full install if you need to change the OpenViking service version.",
        "仅插件升级或回滚不支持 --openviking-version。请使用 --plugin-version 指定插件版本；如果需要调整 OpenViking 服务版本，请执行完整安装流程。",
      ),
    );
    process.exit(1);
  }
}

// Detect OpenClaw version
async function detectOpenClawVersion() {
  try {
    const result = await runCapture("openclaw", ["--version"], { shell: IS_WIN });
    if (result.code === 0 && result.out) {
      const match = result.out.match(/\d+\.\d+(\.\d+)?/);
      if (match) return match[0];
    }
  } catch {}
  return "0.0.0";
}

// Try to fetch a URL, return response text or null
async function tryFetch(url, timeout = 15000) {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    const response = await fetch(url, { signal: controller.signal });
    clearTimeout(timeoutId);
    if (response.ok) {
      return await response.text();
    }
  } catch {}
  return null;
}

// Check if a remote file exists
async function testRemoteFile(url) {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    const response = await fetch(url, { method: "HEAD", signal: controller.signal });
    clearTimeout(timeoutId);
    return response.ok;
  } catch {}
  return false;
}

// Resolve plugin configuration from manifest or fallback
async function resolvePluginConfig() {
  const ghRaw = `https://raw.githubusercontent.com/${REPO}/${PLUGIN_VERSION}`;

  info(tr(`Resolving plugin configuration for version: ${PLUGIN_VERSION}`, `正在解析插件配置，版本: ${PLUGIN_VERSION}`));

  let pluginDir = "";
  let manifestData = null;

  // Try to detect plugin directory and download manifest
  const manifestCurrent = await tryFetch(`${ghRaw}/examples/openclaw-plugin/install-manifest.json`);
  if (manifestCurrent) {
    pluginDir = "openclaw-plugin";
    try {
      manifestData = JSON.parse(manifestCurrent);
    } catch {}
    info(tr("Found manifest in openclaw-plugin", "在 openclaw-plugin 中找到 manifest"));
  } else {
    const manifestLegacy = await tryFetch(`${ghRaw}/examples/openclaw-memory-plugin/install-manifest.json`);
    if (manifestLegacy) {
      pluginDir = "openclaw-memory-plugin";
      try {
        manifestData = JSON.parse(manifestLegacy);
      } catch {}
      info(tr("Found manifest in openclaw-memory-plugin", "在 openclaw-memory-plugin 中找到 manifest"));
    } else if (await testRemoteFile(`${ghRaw}/examples/openclaw-plugin/index.ts`)) {
      pluginDir = "openclaw-plugin";
      info(tr("No manifest found, using fallback for openclaw-plugin", "未找到 manifest，使用 openclaw-plugin 回退配置"));
    } else if (await testRemoteFile(`${ghRaw}/examples/openclaw-memory-plugin/index.ts`)) {
      pluginDir = "openclaw-memory-plugin";
      info(tr("No manifest found, using fallback for openclaw-memory-plugin", "未找到 manifest，使用 openclaw-memory-plugin 回退配置"));
    } else {
      err(tr(`Cannot find plugin directory for version: ${PLUGIN_VERSION}`, `无法找到版本 ${PLUGIN_VERSION} 的插件目录`));
      process.exit(1);
    }
  }

  resolvedPluginDir = pluginDir;
  resolvedPluginReleaseId = "";

  if (manifestData) {
    resolvedPluginId = manifestData.plugin?.id || "";
    resolvedPluginKind = manifestData.plugin?.kind || "";
    resolvedPluginSlot = manifestData.plugin?.slot || "";
    resolvedMinOpenclawVersion = manifestData.compatibility?.minOpenclawVersion || "";
    resolvedMinOpenvikingVersion = manifestData.compatibility?.minOpenvikingVersion || "";
    resolvedPluginReleaseId = manifestData.pluginVersion || manifestData.release?.id || "";
    resolvedNpmOmitDev = manifestData.npm?.omitDev !== false;
    resolvedFilesRequired = manifestData.files?.required || [];
    resolvedFilesOptional = manifestData.files?.optional || [];
  } else {
    // No manifest — determine plugin identity by package.json name
    let fallbackKey = pluginDir === "openclaw-memory-plugin" ? "legacy" : "current";
    let compatVer = "";

    const pkgJson = await tryFetch(`${ghRaw}/examples/${pluginDir}/package.json`);
    if (pkgJson) {
      try {
        const pkg = JSON.parse(pkgJson);
        const pkgName = pkg.name || "";
        resolvedPluginReleaseId = pkg.version || "";
        if (pkgName && pkgName !== "@openclaw/openviking") {
          fallbackKey = "legacy";
          info(tr(`Detected legacy plugin by package name: ${pkgName}`, `通过 package.json 名称检测到旧版插件: ${pkgName}`));
        } else if (pkgName) {
          fallbackKey = "current";
        }
        compatVer = (pkg.engines?.openclaw || "").replace(/^>=?\s*/, "").trim();
        if (compatVer) {
          info(tr(`Read minOpenclawVersion from package.json engines.openclaw: >=${compatVer}`, `从 package.json engines.openclaw 读取到最低版本: >=${compatVer}`));
        }
      } catch {}
    }

    const fallback = fallbackKey === "legacy" ? FALLBACK_LEGACY : FALLBACK_CURRENT;
    resolvedPluginDir = pluginDir;
    resolvedPluginId = fallback.id;
    resolvedPluginKind = fallback.kind;
    resolvedPluginSlot = fallback.slot;
    resolvedFilesRequired = fallback.required;
    resolvedFilesOptional = fallback.optional;
    resolvedNpmOmitDev = true;

    // If no compatVer from package.json, try main branch manifest
    if (!compatVer && PLUGIN_VERSION !== "main") {
      const mainRaw = `https://raw.githubusercontent.com/${REPO}/main`;
      const mainManifest = await tryFetch(`${mainRaw}/examples/openclaw-plugin/install-manifest.json`);
      if (mainManifest) {
        try {
          const m = JSON.parse(mainManifest);
          compatVer = m.compatibility?.minOpenclawVersion || "";
          if (compatVer) {
            info(tr(`Read minOpenclawVersion from main branch manifest: >=${compatVer}`, `从 main 分支 manifest 读取到最低版本: >=${compatVer}`));
          }
        } catch {}
      }
    }

    resolvedMinOpenclawVersion = compatVer || "2026.3.7";
    resolvedMinOpenvikingVersion = "";
  }

  // Set plugin destination
  PLUGIN_DEST = join(OPENCLAW_DIR, "extensions", resolvedPluginId);

  info(tr(`Plugin: ${resolvedPluginId} (${resolvedPluginKind})`, `插件: ${resolvedPluginId} (${resolvedPluginKind})`));
}

// Check OpenClaw version compatibility
async function checkOpenClawCompatibility() {
  if (process.env.SKIP_OPENCLAW === "1") {
    return;
  }

  const ocVersion = await detectOpenClawVersion();
  info(tr(`Detected OpenClaw version: ${ocVersion}`, `检测到 OpenClaw 版本: ${ocVersion}`));

  // If no minimum version required, pass
  if (!resolvedMinOpenclawVersion) {
    return;
  }

  // If user explicitly requested an old version, pass
  if (PLUGIN_VERSION !== "main" && isSemverLike(PLUGIN_VERSION) && !versionGte(PLUGIN_VERSION, "v0.2.8")) {
    return;
  }

  // Check compatibility
  if (!versionGte(ocVersion, resolvedMinOpenclawVersion)) {
    err(tr(
      `OpenClaw ${ocVersion} does not support this plugin (requires >= ${resolvedMinOpenclawVersion})`,
      `OpenClaw ${ocVersion} 不支持此插件（需要 >= ${resolvedMinOpenclawVersion}）`
    ));
    console.log("");
    bold(tr("Please choose one of the following options:", "请选择以下方案之一："));
    console.log("");
    console.log(`  ${tr("Option 1: Upgrade OpenClaw", "方案 1：升级 OpenClaw")}`);
    console.log(`    npm update -g openclaw --registry ${NPM_REGISTRY}`);
    console.log("");
    console.log(`  ${tr("Option 2: Install a legacy plugin release compatible with your current OpenClaw version", "方案 2：安装与当前 OpenClaw 版本兼容的旧版插件")}`);
    console.log(`    ${getLegacyInstallCommandHint()}`);
    console.log("");
    process.exit(1);
  }
}

function checkRequestedOpenVikingCompatibility() {
  if (!resolvedMinOpenvikingVersion || !openvikingVersion) return;
  if (versionGte(openvikingVersion, resolvedMinOpenvikingVersion)) return;

  err(tr(
    `OpenViking ${openvikingVersion} does not support this plugin (requires >= ${resolvedMinOpenvikingVersion})`,
    `OpenViking ${openvikingVersion} 不支持此插件（需要 >= ${resolvedMinOpenvikingVersion}）`,
  ));
  console.log("");
  console.log(tr(
    "Use a newer OpenViking version, or omit --openviking-version to install the latest release.",
    "请使用更新版本的 OpenViking，或省略 --openviking-version 以安装最新版本。",
  ));
  process.exit(1);
}

async function installOpenViking() {
  if (process.env.SKIP_OPENVIKING === "1") {
    info(tr("Skipping OpenViking install (SKIP_OPENVIKING=1)", "跳过 OpenViking 安装 (SKIP_OPENVIKING=1)"));
    return;
  }

  const python = await checkPython();
  if (!python.cmd) {
    err(tr("Python check failed.", "Python 校验失败"));
    process.exit(1);
  }
  if (!python.ok) {
    warn(tr(
      `${python.detail}. Will attempt to find a suitable Python for pip install.`,
      `${python.detail}。将尝试查找合适的 Python 进行 pip 安装。`,
    ));
  }

  const py = python.cmd;

  if (openvikingRepo && existsSync(join(openvikingRepo, "pyproject.toml"))) {
    info(tr(`Installing OpenViking from source (editable): ${openvikingRepo}`, `正在从源码安装 OpenViking（可编辑）: ${openvikingRepo}`));
    await run(py, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", PIP_INDEX_URL], { silent: true });
    await run(py, ["-m", "pip", "install", "-e", openvikingRepo]);
    openvikingPythonPath = py;
    info(tr("OpenViking installed ✓ (source)", "OpenViking 安装完成 ✓（源码）"));
    return;
  }

  // Determine package spec
  const pkgSpec = openvikingVersion ? `openviking==${openvikingVersion}` : "openviking";
  if (openvikingVersion) {
    info(tr(`Installing OpenViking ${openvikingVersion} from PyPI...`, `正在安装 OpenViking ${openvikingVersion} (PyPI)...`));
  } else {
    info(tr("Installing OpenViking (latest) from PyPI...", "正在安装 OpenViking (最新版) (PyPI)..."));
  }
  info(tr(`Using pip index: ${PIP_INDEX_URL}`, `使用 pip 镜像源: ${PIP_INDEX_URL}`));

  info(`Package: ${pkgSpec}`);
  await runCapture(py, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", PIP_INDEX_URL], { shell: false });
  const installResult = await runLiveCapture(
    py,
    ["-m", "pip", "install", "--progress-bar", "on", pkgSpec, "-i", PIP_INDEX_URL],
    { shell: false },
  );
  if (installResult.code === 0) {
    openvikingPythonPath = py;
    info(tr("OpenViking installed ✓", "OpenViking 安装完成 ✓"));
    return;
  }

  const installOutput = `${installResult.out}\n${installResult.err}`;
  const shouldTryVenv = !IS_WIN && /externally-managed-environment|externally managed|No module named pip/i.test(installOutput);
  if (shouldTryVenv) {
    const venvDir = join(OPENVIKING_DIR, "venv");
    const venvPy = IS_WIN ? join(venvDir, "Scripts", "python.exe") : join(venvDir, "bin", "python");

    if (existsSync(venvPy)) {
      const reuseCheck = await runCapture(venvPy, ["-c", "import openviking"], { shell: false });
      if (reuseCheck.code === 0) {
        await runLiveCapture(
          venvPy,
          ["-m", "pip", "install", "--progress-bar", "on", "-U", pkgSpec, "-i", PIP_INDEX_URL],
          { shell: false },
        );
        openvikingPythonPath = venvPy;
        info(tr("OpenViking installed ✓ (venv)", "OpenViking 安装完成 ✓（虚拟环境）"));
        return;
      }
    }

    await mkdir(OPENVIKING_DIR, { recursive: true });
    const venvCreate = await runCapture(py, ["-m", "venv", venvDir], { shell: false });
    if (venvCreate.code !== 0) {
      console.log("");
      err(tr("Cannot create Python virtual environment.", "无法创建 Python 虚拟环境。"));
      console.log(tr(
        "  python3-venv is not installed. Fix with:",
        "  python3-venv 未安装，请执行以下命令修复："
      ));
      console.log(`
  apt update
  apt install -y software-properties-common
  add-apt-repository universe
  apt update
  apt install -y python3-venv
`);
      console.log(tr(
        "  Or force install into system Python (not recommended):",
        "  或强制安装到系统 Python（不推荐）："
      ));
      console.log(`  OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1 ov-install\n`);
      process.exit(1);
    }

    await runCapture(venvPy, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", PIP_INDEX_URL], { shell: false });
    const venvInstall = await runLiveCapture(
      venvPy,
      ["-m", "pip", "install", "--progress-bar", "on", pkgSpec, "-i", PIP_INDEX_URL],
      { shell: false },
    );
    if (venvInstall.code === 0) {
      openvikingPythonPath = venvPy;
      info(tr("OpenViking installed ✓ (venv)", "OpenViking 安装完成 ✓（虚拟环境）"));
      return;
    }

    err(tr("OpenViking install failed in venv.", "在虚拟环境中安装 OpenViking 失败。"));
    console.log(venvInstall.err || venvInstall.out);
    process.exit(1);
  }

  if (process.env.OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES === "1") {
    const systemInstall = await runLiveCapture(
      py,
      ["-m", "pip", "install", "--progress-bar", "on", "--break-system-packages", pkgSpec, "-i", PIP_INDEX_URL],
      { shell: false },
    );
    if (systemInstall.code === 0) {
      openvikingPythonPath = py;
      info(tr("OpenViking installed ✓ (system)", "OpenViking 安装完成 ✓（系统）"));
      return;
    }
  }

  err(tr("OpenViking install failed. Check Python >= 3.10 and pip.", "OpenViking 安装失败，请检查 Python >= 3.10 及 pip"));
  console.log(installResult.err || installResult.out);
  process.exit(1);
}

async function configureOvConf() {
  await mkdir(OPENVIKING_DIR, { recursive: true });

  let workspace = join(OPENVIKING_DIR, "data");
  let serverPort = String(DEFAULT_SERVER_PORT);
  let agfsPort = String(DEFAULT_AGFS_PORT);
  let vlmModel = DEFAULT_VLM_MODEL;
  let embeddingModel = DEFAULT_EMBED_MODEL;
  let vlmApiKey = process.env.OPENVIKING_VLM_API_KEY || process.env.OPENVIKING_ARK_API_KEY || "";
  let embeddingApiKey = process.env.OPENVIKING_EMBEDDING_API_KEY || process.env.OPENVIKING_ARK_API_KEY || "";

  if (!installYes) {
    console.log("");
    workspace = await question(tr("OpenViking workspace path", "OpenViking 数据目录"), workspace);
    serverPort = await question(tr("OpenViking HTTP port", "OpenViking HTTP 端口"), serverPort);
    agfsPort = await question(tr("AGFS port", "AGFS 端口"), agfsPort);
    vlmModel = await question(tr("VLM model", "VLM 模型"), vlmModel);
    embeddingModel = await question(tr("Embedding model", "Embedding 模型"), embeddingModel);
    console.log(tr("VLM and Embedding API keys can differ. Leave empty to edit ov.conf later.", "说明：VLM 与 Embedding 的 API Key 可分别填写，留空可稍后在 ov.conf 修改。"));
    const vlmInput = await question(tr("VLM API key (optional)", "VLM API Key（可留空）"), "");
    const embInput = await question(tr("Embedding API key (optional)", "Embedding API Key（可留空）"), "");
    if (vlmInput) vlmApiKey = vlmInput;
    if (embInput) embeddingApiKey = embInput;
  }

  selectedServerPort = Number.parseInt(serverPort, 10) || DEFAULT_SERVER_PORT;
  const agfsPortNum = Number.parseInt(agfsPort, 10) || DEFAULT_AGFS_PORT;

  await mkdir(workspace, { recursive: true });

  const config = {
    server: {
      host: "127.0.0.1",
      port: selectedServerPort,
      root_api_key: null,
      cors_origins: ["*"],
    },
    storage: {
      workspace,
      vectordb: { name: "context", backend: "local", project: "default" },
      agfs: { port: agfsPortNum, log_level: "warn", backend: "local", timeout: 10, retry_times: 3 },
    },
    log: {
      level: "WARNING",
      format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
      output: "file",
      rotation: true,
      rotation_days: 3,
      rotation_interval: "midnight",
    },
    embedding: {
      dense: {
        provider: "volcengine",
        api_key: embeddingApiKey || null,
        model: embeddingModel,
        api_base: "https://ark.cn-beijing.volces.com/api/v3",
        dimension: 1024,
        input: "multimodal",
      },
    },
    vlm: {
      provider: "volcengine",
      api_key: vlmApiKey || null,
      model: vlmModel,
      api_base: "https://ark.cn-beijing.volces.com/api/v3",
      temperature: 0.1,
      max_retries: 3,
    },
  };

  const configPath = join(OPENVIKING_DIR, "ov.conf");
  await writeFile(configPath, JSON.stringify(config, null, 2) + "\n", "utf8");
  info(tr(`Config generated: ${configPath}`, `已生成配置: ${configPath}`));
}

function getOpenClawConfigPath() {
  return join(OPENCLAW_DIR, "openclaw.json");
}

function getOpenClawEnv() {
  if (OPENCLAW_DIR === DEFAULT_OPENCLAW_DIR) {
    return { ...process.env };
  }
  return { ...process.env, OPENCLAW_STATE_DIR: OPENCLAW_DIR };
}

async function readJsonFileIfExists(filePath) {
  if (!existsSync(filePath)) return null;
  const raw = await readFile(filePath, "utf8");
  return JSON.parse(raw);
}

function getInstallStatePathForPlugin(pluginId) {
  return join(OPENCLAW_DIR, "extensions", pluginId, ".ov-install-state.json");
}

function getUpgradeAuditDir() {
  return join(OPENCLAW_DIR, ".openviking-upgrade-backup");
}

function getUpgradeAuditPath() {
  return join(getUpgradeAuditDir(), "last-upgrade.json");
}

function getOpenClawConfigBackupPath() {
  return join(getUpgradeAuditDir(), "openclaw.json.bak");
}

function normalizePluginMode(value) {
  return value === "remote" ? "remote" : "local";
}

function getPluginVariantById(pluginId) {
  return PLUGIN_VARIANTS.find((variant) => variant.id === pluginId) || null;
}

function detectPluginPresence(config, variant) {
  const plugins = config?.plugins;
  const reasons = [];
  if (!plugins) {
    return { variant, present: false, reasons };
  }

  if (plugins.entries && Object.prototype.hasOwnProperty.call(plugins.entries, variant.id)) {
    reasons.push("entry");
  }
  if (plugins.slots?.[variant.slot] === variant.id) {
    reasons.push("slot");
  }
  if (Array.isArray(plugins.allow) && plugins.allow.includes(variant.id)) {
    reasons.push("allow");
  }
  if (
    Array.isArray(plugins.load?.paths)
    && plugins.load.paths.some((item) => typeof item === "string" && (item.includes(variant.id) || item.includes(variant.dir)))
  ) {
    reasons.push("loadPath");
  }
  if (existsSync(join(OPENCLAW_DIR, "extensions", variant.id))) {
    reasons.push("dir");
  }

  return { variant, present: reasons.length > 0, reasons };
}

async function detectInstalledPluginState() {
  const configPath = getOpenClawConfigPath();
  const config = await readJsonFileIfExists(configPath);
  const detections = [];
  for (const variant of PLUGIN_VARIANTS) {
    const detection = detectPluginPresence(config, variant);
    if (!detection.present) continue;
    detection.installState = await readJsonFileIfExists(getInstallStatePathForPlugin(variant.id));
    detections.push(detection);
  }

  let generation = "none";
  if (detections.length === 1) {
    generation = detections[0].variant.generation;
  } else if (detections.length > 1) {
    generation = "mixed";
  }

  return {
    config,
    configPath,
    detections,
    generation,
  };
}

function formatInstalledDetectionLabel(detection) {
  const requestedRef = detection.installState?.requestedRef;
  const releaseId = detection.installState?.releaseId;
  if (requestedRef) return `${detection.variant.id}@${requestedRef}`;
  if (releaseId) return `${detection.variant.id}#${releaseId}`;
  return `${detection.variant.id} (${detection.variant.generation}, exact version unknown)`;
}

function formatInstalledStateLabel(installedState) {
  if (!installedState?.detections?.length) {
    return "not-installed";
  }
  return installedState.detections.map(formatInstalledDetectionLabel).join(" + ");
}

function formatTargetVersionLabel() {
  const base = `${resolvedPluginId || "openviking"}@${PLUGIN_VERSION}`;
  if (resolvedPluginReleaseId && resolvedPluginReleaseId !== PLUGIN_VERSION) {
    return `${base} (${resolvedPluginReleaseId})`;
  }
  return base;
}

function extractRuntimeConfigFromPluginEntry(entryConfig) {
  if (!entryConfig || typeof entryConfig !== "object") return null;

  const mode = normalizePluginMode(entryConfig.mode);
  const runtime = { mode };

  if (mode === "remote") {
    if (typeof entryConfig.baseUrl === "string" && entryConfig.baseUrl.trim()) {
      runtime.baseUrl = entryConfig.baseUrl.trim();
    }
    if (typeof entryConfig.apiKey === "string" && entryConfig.apiKey.trim()) {
      runtime.apiKey = entryConfig.apiKey;
    }
    if (typeof entryConfig.agentId === "string" && entryConfig.agentId.trim()) {
      runtime.agentId = entryConfig.agentId.trim();
    }
    return runtime;
  }

  if (typeof entryConfig.configPath === "string" && entryConfig.configPath.trim()) {
    runtime.configPath = entryConfig.configPath.trim();
  }
  if (entryConfig.port !== undefined && entryConfig.port !== null && `${entryConfig.port}`.trim()) {
    const parsedPort = Number.parseInt(String(entryConfig.port), 10);
    if (Number.isFinite(parsedPort) && parsedPort > 0) {
      runtime.port = parsedPort;
    }
  }
  return runtime;
}

async function readPortFromOvConf(configPath) {
  const filePath = configPath || join(OPENVIKING_DIR, "ov.conf");
  if (!existsSync(filePath)) return null;
  try {
    const ovConf = await readJsonFileIfExists(filePath);
    const parsedPort = Number.parseInt(String(ovConf?.server?.port ?? ""), 10);
    return Number.isFinite(parsedPort) && parsedPort > 0 ? parsedPort : null;
  } catch {
    return null;
  }
}

async function backupOpenClawConfig(configPath) {
  await mkdir(getUpgradeAuditDir(), { recursive: true });
  const backupPath = getOpenClawConfigBackupPath();
  const configText = await readFile(configPath, "utf8");
  await writeFile(backupPath, configText, "utf8");
  return backupPath;
}

async function writeUpgradeAuditFile(data) {
  await mkdir(getUpgradeAuditDir(), { recursive: true });
  await writeFile(getUpgradeAuditPath(), `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

async function writeInstallStateFile({ operation, fromVersion, configBackupPath, pluginBackups }) {
  const installStatePath = getInstallStatePathForPlugin(resolvedPluginId || "openviking");
  const state = {
    pluginId: resolvedPluginId || "openviking",
    generation: getPluginVariantById(resolvedPluginId || "openviking")?.generation || "unknown",
    requestedRef: PLUGIN_VERSION,
    releaseId: resolvedPluginReleaseId || "",
    operation,
    fromVersion: fromVersion || "",
    configBackupPath: configBackupPath || "",
    pluginBackups: pluginBackups || [],
    installedAt: new Date().toISOString(),
    repo: REPO,
  };
  await writeFile(installStatePath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
}

async function moveDirWithFallback(sourceDir, destDir) {
  try {
    await rename(sourceDir, destDir);
  } catch {
    await cp(sourceDir, destDir, { recursive: true, force: true });
    await rm(sourceDir, { recursive: true, force: true });
  }
}

async function rollbackLastUpgradeOperation() {
  const auditPath = getUpgradeAuditPath();
  const audit = await readJsonFileIfExists(auditPath);
  if (!audit) {
    err(
      tr(
        `No rollback audit file found at ${auditPath}.`,
        `未找到回滚审计文件: ${auditPath}`,
      ),
    );
    process.exit(1);
  }

  if (audit.rolledBackAt) {
    warn(
      tr(
        `The last recorded upgrade was already rolled back at ${audit.rolledBackAt}.`,
        `最近一次升级已在 ${audit.rolledBackAt} 回滚。`,
      ),
    );
  }

  const configBackupPath = audit.configBackupPath || getOpenClawConfigBackupPath();
  if (!existsSync(configBackupPath)) {
    err(
      tr(
        `Rollback config backup is missing: ${configBackupPath}`,
        `回滚配置备份缺失: ${configBackupPath}`,
      ),
    );
    process.exit(1);
  }

  const pluginBackups = Array.isArray(audit.pluginBackups) ? audit.pluginBackups : [];
  if (pluginBackups.length === 0) {
    err(tr("Rollback audit file contains no plugin backups.", "回滚审计文件中没有插件备份信息。"));
    process.exit(1);
  }
  for (const pluginBackup of pluginBackups) {
    if (!pluginBackup?.pluginId || !pluginBackup?.backupDir || !existsSync(pluginBackup.backupDir)) {
      err(
        tr(
          `Rollback plugin backup is missing: ${pluginBackup?.backupDir || "<unknown>"}`,
          `回滚插件备份缺失: ${pluginBackup?.backupDir || "<unknown>"}`,
        ),
      );
      process.exit(1);
    }
  }

  info(tr(`Rolling back last upgrade: ${audit.fromVersion || "unknown"} <- ${audit.toVersion || "unknown"}`, `开始回滚最近一次升级: ${audit.fromVersion || "unknown"} <- ${audit.toVersion || "unknown"}`));
  await stopOpenClawGatewayForUpgrade();

  const configText = await readFile(configBackupPath, "utf8");
  await writeFile(getOpenClawConfigPath(), configText, "utf8");
  info(tr(`Restored openclaw.json from backup: ${configBackupPath}`, `已从备份恢复 openclaw.json: ${configBackupPath}`));

  const extensionsDir = join(OPENCLAW_DIR, "extensions");
  await mkdir(extensionsDir, { recursive: true });
  for (const variant of PLUGIN_VARIANTS) {
    const liveDir = join(extensionsDir, variant.id);
    if (existsSync(liveDir)) {
      await rm(liveDir, { recursive: true, force: true });
    }
  }

  for (const pluginBackup of pluginBackups) {
    if (!pluginBackup?.pluginId || !pluginBackup?.backupDir) continue;
    if (!existsSync(pluginBackup.backupDir)) {
      err(
        tr(
          `Rollback plugin backup is missing: ${pluginBackup.backupDir}`,
          `回滚插件备份缺失: ${pluginBackup.backupDir}`,
        ),
      );
      process.exit(1);
    }
    const destDir = join(extensionsDir, pluginBackup.pluginId);
    await moveDirWithFallback(pluginBackup.backupDir, destDir);
    info(tr(`Restored plugin directory: ${destDir}`, `已恢复插件目录: ${destDir}`));
  }

  audit.rolledBackAt = new Date().toISOString();
  audit.rollbackConfigPath = configBackupPath;
  await writeUpgradeAuditFile(audit);

  console.log("");
  bold(tr("Rollback complete!", "回滚完成！"));
  console.log("");
  info(tr(`Rollback audit file: ${auditPath}`, `回滚审计文件: ${auditPath}`));
  info(tr("Run `openclaw gateway` and `openclaw status` to verify the restored plugin state.", "请运行 `openclaw gateway` 和 `openclaw status` 验证恢复后的插件状态。"));
}

async function prepareUpgradeRuntimeConfig(installedState) {
  const plugins = installedState.config?.plugins ?? {};
  const candidateOrder = installedState.detections
    .map((item) => item.variant)
    .sort((left, right) => (right.generation === "current" ? 1 : 0) - (left.generation === "current" ? 1 : 0));

  let runtime = null;
  for (const variant of candidateOrder) {
    const entryConfig = extractRuntimeConfigFromPluginEntry(plugins.entries?.[variant.id]?.config);
    if (entryConfig) {
      runtime = entryConfig;
      break;
    }
  }

  if (!runtime) {
    runtime = { mode: "local" };
  }

  if (runtime.mode === "remote") {
    runtime.baseUrl = runtime.baseUrl || remoteBaseUrl;
    return runtime;
  }

  runtime.configPath = runtime.configPath || join(OPENVIKING_DIR, "ov.conf");
  runtime.port = runtime.port || await readPortFromOvConf(runtime.configPath) || DEFAULT_SERVER_PORT;
  return runtime;
}

function removePluginConfig(config, variant) {
  const plugins = config?.plugins;
  if (!plugins) return false;

  let changed = false;

  if (Array.isArray(plugins.allow)) {
    const nextAllow = plugins.allow.filter((item) => item !== variant.id);
    changed = changed || nextAllow.length !== plugins.allow.length;
    plugins.allow = nextAllow;
  }

  if (Array.isArray(plugins.load?.paths)) {
    const nextPaths = plugins.load.paths.filter(
      (item) => typeof item !== "string" || (!item.includes(variant.id) && !item.includes(variant.dir)),
    );
    changed = changed || nextPaths.length !== plugins.load.paths.length;
    plugins.load.paths = nextPaths;
  }

  if (plugins.entries && Object.prototype.hasOwnProperty.call(plugins.entries, variant.id)) {
    delete plugins.entries[variant.id];
    changed = true;
  }

  if (plugins.slots?.[variant.slot] === variant.id) {
    plugins.slots[variant.slot] = variant.slotFallback;
    changed = true;
  }

  return changed;
}

async function prunePreviousUpgradeBackups(disabledDir, variant, keepDir) {
  if (!existsSync(disabledDir)) return;

  const prefix = `${variant.id}-upgrade-backup-`;
  const keepName = keepDir ? keepDir.split(/[\\/]/).pop() : "";
  const entries = readdirSync(disabledDir, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    if (!entry.name.startsWith(prefix)) continue;
    if (keepName && entry.name === keepName) continue;
    await rm(join(disabledDir, entry.name), { recursive: true, force: true });
  }
}

async function backupPluginDirectory(variant) {
  const pluginDir = join(OPENCLAW_DIR, "extensions", variant.id);
  if (!existsSync(pluginDir)) return null;

  const disabledDir = join(OPENCLAW_DIR, "disabled-extensions");
  const backupDir = join(disabledDir, `${variant.id}-upgrade-backup-${Date.now()}`);
  await mkdir(disabledDir, { recursive: true });
  try {
    await rename(pluginDir, backupDir);
  } catch {
    await cp(pluginDir, backupDir, { recursive: true, force: true });
    await rm(pluginDir, { recursive: true, force: true });
  }
  info(tr(`Backed up plugin directory: ${backupDir}`, `已备份插件目录: ${backupDir}`));
  await prunePreviousUpgradeBackups(disabledDir, variant, backupDir);
  return backupDir;
}

async function stopOpenClawGatewayForUpgrade() {
  const result = await runCapture("openclaw", ["gateway", "stop"], {
    env: getOpenClawEnv(),
    shell: IS_WIN,
  });
  if (result.code === 0) {
    info(tr("Stopped OpenClaw gateway before plugin upgrade", "升级插件前已停止 OpenClaw gateway"));
  } else {
    warn(tr("OpenClaw gateway may not be running; continuing", "OpenClaw gateway 可能未在运行，继续执行"));
  }
}

function shouldClaimTargetSlot(installedState) {
  const currentOwner = installedState.config?.plugins?.slots?.[resolvedPluginSlot];
  if (!currentOwner || currentOwner === "none" || currentOwner === "legacy" || currentOwner === resolvedPluginId) {
    return true;
  }
  const currentOwnerVariant = getPluginVariantById(currentOwner);
  if (currentOwnerVariant && installedState.detections.some((item) => item.variant.id === currentOwnerVariant.id)) {
    return true;
  }
  return false;
}

async function cleanupInstalledPluginConfig(installedState) {
  if (!installedState.config || !installedState.config.plugins) {
    warn(tr("openclaw.json has no plugins section; skipped targeted plugin cleanup", "openclaw.json 中没有 plugins 配置，已跳过定向插件清理"));
    return;
  }

  const nextConfig = structuredClone(installedState.config);
  let changed = false;
  for (const detection of installedState.detections) {
    changed = removePluginConfig(nextConfig, detection.variant) || changed;
  }

  if (!changed) {
    info(tr("No OpenViking plugin config changes were required", "无需修改 OpenViking 插件配置"));
    return;
  }

  await writeFile(installedState.configPath, `${JSON.stringify(nextConfig, null, 2)}\n`, "utf8");
  info(tr("Cleaned existing OpenViking plugin config only", "已仅清理 OpenViking 自身插件配置"));
}

async function prepareStrongPluginUpgrade() {
  const installedState = await detectInstalledPluginState();
  if (installedState.generation === "none") {
    err(
      tr(
        "Plugin upgrade mode requires an existing OpenViking plugin entry in openclaw.json.",
        "插件升级模式要求 openclaw.json 中已经存在 OpenViking 插件记录。",
      ),
    );
    process.exit(1);
  }

  installedUpgradeState = installedState;
  upgradeRuntimeConfig = await prepareUpgradeRuntimeConfig(installedState);
  const fromVersion = formatInstalledStateLabel(installedState);
  const toVersion = formatTargetVersionLabel();
  selectedMode = upgradeRuntimeConfig.mode;
  info(
    tr(
      `Detected installed OpenViking plugin state: ${installedState.generation}`,
      `检测到已安装 OpenViking 插件状态: ${installedState.generation}`,
    ),
  );
  if (upgradeRuntimeConfig.mode === "remote") {
    remoteBaseUrl = upgradeRuntimeConfig.baseUrl || remoteBaseUrl;
    remoteApiKey = upgradeRuntimeConfig.apiKey || "";
    remoteAgentId = upgradeRuntimeConfig.agentId || "";
  } else {
    selectedServerPort = upgradeRuntimeConfig.port || DEFAULT_SERVER_PORT;
  }
  info(tr(`Upgrade runtime mode: ${selectedMode}`, `升级运行模式: ${selectedMode}`));

  info(tr(`Upgrade path: ${fromVersion} -> ${toVersion}`, `升级路径: ${fromVersion} -> ${toVersion}`));

  await stopOpenClawGatewayForUpgrade();
  const configBackupPath = await backupOpenClawConfig(installedState.configPath);
  info(tr(`Backed up openclaw.json: ${configBackupPath}`, `已备份 openclaw.json: ${configBackupPath}`));
  const pluginBackups = [];
  for (const detection of installedState.detections) {
    const backupDir = await backupPluginDirectory(detection.variant);
    if (backupDir) {
      pluginBackups.push({ pluginId: detection.variant.id, backupDir });
    }
  }
  upgradeAudit = {
    operation: "upgrade",
    createdAt: new Date().toISOString(),
    fromVersion,
    toVersion,
    configBackupPath,
    pluginBackups,
    runtimeMode: selectedMode,
  };
  await writeUpgradeAuditFile(upgradeAudit);
  await cleanupInstalledPluginConfig(installedState);

  info(
    tr(
      "Upgrade will keep the existing OpenViking runtime file and re-apply only the minimum plugin runtime settings.",
      "升级将保留现有 OpenViking 运行时文件，并只回填最小插件运行配置。",
    ),
  );
  info(tr(`Upgrade audit file: ${getUpgradeAuditPath()}`, `升级审计文件: ${getUpgradeAuditPath()}`));
}

async function downloadPluginFile(destDir, fileName, url, required, index, total) {
  const maxRetries = 3;
  const destPath = join(destDir, fileName);

  process.stdout.write(`  [${index}/${total}] ${fileName} `);

  let lastStatus = 0;
  let saw404 = false;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(url);
      lastStatus = response.status;
      if (response.ok) {
        const buffer = Buffer.from(await response.arrayBuffer());
        if (buffer.length === 0) {
          lastStatus = 0;
        } else {
          await mkdir(dirname(destPath), { recursive: true });
          await writeFile(destPath, buffer);
          console.log(" OK");
          return;
        }
      } else if (!required && response.status === 404) {
        saw404 = true;
        break;
      }
    } catch {
      lastStatus = 0;
    }

    if (attempt < maxRetries) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }

  if (saw404 || lastStatus === 404) {
    if (fileName === ".gitignore") {
      await mkdir(dirname(destPath), { recursive: true });
      await writeFile(destPath, "node_modules/\n", "utf8");
      console.log(" OK");
      return;
    }
    console.log(tr(" skip", " 跳过"));
    return;
  }

  if (!required) {
    console.log("");
    err(
      tr(
        `Optional file failed after ${maxRetries} retries (HTTP ${lastStatus || "network"}): ${url}`,
        `可选文件已重试 ${maxRetries} 次仍失败（HTTP ${lastStatus || "网络错误"}）: ${url}`,
      ),
    );
    process.exit(1);
  }

  console.log("");
  err(tr(`Download failed after ${maxRetries} retries: ${url}`, `下载失败（已重试 ${maxRetries} 次）: ${url}`));
  process.exit(1);
}

async function downloadPlugin(destDir) {
  const ghRaw = `https://raw.githubusercontent.com/${REPO}/${PLUGIN_VERSION}`;
  const pluginDir = resolvedPluginDir;
  const total = resolvedFilesRequired.length + resolvedFilesOptional.length;

  await mkdir(destDir, { recursive: true });

  info(tr(`Downloading plugin from ${REPO}@${PLUGIN_VERSION} (${total} files)...`, `正在从 ${REPO}@${PLUGIN_VERSION} 下载插件（共 ${total} 个文件）...`));

  let i = 0;
  // Download required files
  for (const name of resolvedFilesRequired) {
    if (!name) continue;
    i++;
    const url = `${ghRaw}/examples/${pluginDir}/${name}`;
    await downloadPluginFile(destDir, name, url, true, i, total);
  }

  // Download optional files
  for (const name of resolvedFilesOptional) {
    if (!name) continue;
    i++;
    const url = `${ghRaw}/examples/${pluginDir}/${name}`;
    await downloadPluginFile(destDir, name, url, false, i, total);
  }

  // npm install
  info(tr("Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
  const npmArgs = resolvedNpmOmitDev
    ? ["install", "--omit=dev", "--no-audit", "--no-fund", "--registry", NPM_REGISTRY]
    : ["install", "--no-audit", "--no-fund", "--registry", NPM_REGISTRY];
  await run("npm", npmArgs, { cwd: destDir, silent: false });
  info(tr(`Plugin deployed: ${PLUGIN_DEST}`, `插件部署完成: ${PLUGIN_DEST}`));
}

async function deployLocalPlugin(localPluginDir, destDir) {
  await rm(destDir, { recursive: true, force: true });
  await mkdir(destDir, { recursive: true });
  await cp(localPluginDir, destDir, {
    recursive: true,
    force: true,
    filter: (sourcePath) => {
      const rel = relative(localPluginDir, sourcePath);
      if (!rel) return true;
      const firstSegment = rel.split(/[\\/]/)[0];
      return firstSegment !== "node_modules" && firstSegment !== ".git";
    },
  });
}

async function installPluginDependencies(destDir) {
  info(tr("Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
  const npmArgs = resolvedNpmOmitDev
    ? ["install", "--omit=dev", "--no-audit", "--no-fund", "--registry", NPM_REGISTRY]
    : ["install", "--no-audit", "--no-fund", "--registry", NPM_REGISTRY];
  await run("npm", npmArgs, { cwd: destDir, silent: false });
  return info(tr(`Plugin prepared: ${destDir}`, `插件已准备: ${destDir}`));
}

async function createPluginStagingDir() {
  const pluginId = resolvedPluginId || "openviking";
  const extensionsDir = join(OPENCLAW_DIR, "extensions");
  const stagingDir = join(extensionsDir, `.${pluginId}.staging-${process.pid}-${Date.now()}`);
  await mkdir(extensionsDir, { recursive: true });
  await rm(stagingDir, { recursive: true, force: true });
  await mkdir(stagingDir, { recursive: true });
  return stagingDir;
}

async function finalizePluginDeployment(stagingDir) {
  await rm(PLUGIN_DEST, { recursive: true, force: true });
  try {
    await rename(stagingDir, PLUGIN_DEST);
  } catch {
    await cp(stagingDir, PLUGIN_DEST, { recursive: true, force: true });
    await rm(stagingDir, { recursive: true, force: true });
  }
  return info(tr(`Plugin deployed: ${PLUGIN_DEST}`, `插件部署完成: ${PLUGIN_DEST}`));
}

async function deployPluginFromRemote() {
  const stagingDir = await createPluginStagingDir();
  try {
    await downloadPlugin(stagingDir);
    await finalizePluginDeployment(stagingDir);
  } catch (error) {
    await rm(stagingDir, { recursive: true, force: true });
    throw error;
  }
}

/** Same as INSTALL*.md manual cleanup: stale entries block `plugins.slots.*` validation after reinstall. */
function resolvedPluginSlotFallback() {
  if (resolvedPluginId === "memory-openviking") return "none";
  if (resolvedPluginId === "openviking") return "legacy";
  return "none";
}

async function scrubStaleOpenClawPluginRegistration() {
  const configPath = getOpenClawConfigPath();
  if (!existsSync(configPath)) return;
  const pluginId = resolvedPluginId;
  const slot = resolvedPluginSlot;
  const slotFallback = resolvedPluginSlotFallback();
  let raw;
  try {
    raw = await readFile(configPath, "utf8");
  } catch {
    return;
  }
  let cfg;
  try {
    cfg = JSON.parse(raw);
  } catch {
    return;
  }
  if (!cfg.plugins) return;
  const p = cfg.plugins;
  let changed = false;
  if (p.entries && Object.prototype.hasOwnProperty.call(p.entries, pluginId)) {
    delete p.entries[pluginId];
    changed = true;
  }
  if (Array.isArray(p.allow)) {
    const next = p.allow.filter((id) => id !== pluginId);
    if (next.length !== p.allow.length) {
      p.allow = next;
      changed = true;
    }
  }
  if (p.load && Array.isArray(p.load.paths)) {
    const norm = (s) => String(s).replace(/\\/g, "/");
    const extNeedle = `/extensions/${pluginId}`;
    const next = p.load.paths.filter((path) => {
      if (typeof path !== "string") return true;
      return !norm(path).includes(extNeedle);
    });
    if (next.length !== p.load.paths.length) {
      p.load.paths = next;
      changed = true;
    }
  }
  if (p.slots && p.slots[slot] === pluginId) {
    p.slots[slot] = slotFallback;
    changed = true;
  }
  if (!changed) return;
  const out = JSON.stringify(cfg, null, 2) + "\n";
  const tmp = `${configPath}.ov-install-tmp.${process.pid}`;
  await writeFile(tmp, out, "utf8");
  await rename(tmp, configPath);
}

async function deployPluginFromLocal(localPluginDir) {
  const stagingDir = await createPluginStagingDir();
  try {
    await deployLocalPlugin(localPluginDir, stagingDir);
    await installPluginDependencies(stagingDir);
    await finalizePluginDeployment(stagingDir);
  } catch (error) {
    await rm(stagingDir, { recursive: true, force: true });
    throw error;
  }
}

async function configureOpenClawPlugin({
  preserveExistingConfig = false,
  runtimeConfig = null,
  skipGatewayMode = false,
  claimSlot = true,
} = {}) {
  info(tr("Configuring OpenClaw plugin...", "正在配置 OpenClaw 插件..."));

  const pluginId = resolvedPluginId;
  const pluginSlot = resolvedPluginSlot;

  const ocEnv = getOpenClawEnv();

  const oc = async (args) => {
    const result = await runCapture("openclaw", args, { env: ocEnv, shell: IS_WIN });
    if (result.code !== 0) {
      const detail = result.err || result.out;
      throw new Error(`openclaw ${args.join(" ")} failed (exit code ${result.code})${detail ? `: ${detail}` : ""}`);
    }
    return result;
  };

  if (!preserveExistingConfig) {
    await scrubStaleOpenClawPluginRegistration();
  }

  // Enable plugin (files already deployed to extensions dir by deployPlugin)
  await oc(["plugins", "enable", pluginId]);
  if (claimSlot) {
    await oc(["config", "set", `plugins.slots.${pluginSlot}`, pluginId]);
  } else {
    warn(
      tr(
        `Skipped claiming plugins.slots.${pluginSlot}; it is currently owned by another plugin.`,
        `已跳过设置 plugins.slots.${pluginSlot}，当前该 slot 由其他插件占用。`,
      ),
    );
  }

  if (preserveExistingConfig) {
    info(
      tr(
        `Preserved existing plugin runtime config for ${pluginId}`,
        `已保留 ${pluginId} 的现有插件运行时配置`,
      ),
    );
    return;
  }

  const effectiveRuntimeConfig = runtimeConfig || (
    selectedMode === "remote"
      ? { mode: "remote", baseUrl: remoteBaseUrl, apiKey: remoteApiKey, agentId: remoteAgentId }
      : { mode: "local", configPath: join(OPENVIKING_DIR, "ov.conf"), port: selectedServerPort }
  );

  if (!skipGatewayMode) {
    await oc(["config", "set", "gateway.mode", effectiveRuntimeConfig.mode === "remote" ? "remote" : "local"]);
  }

  // Set plugin config for the selected mode
  if (effectiveRuntimeConfig.mode === "local") {
    const ovConfPath = effectiveRuntimeConfig.configPath || join(OPENVIKING_DIR, "ov.conf");
    await oc(["config", "set", `plugins.entries.${pluginId}.config.mode`, "local"]);
    await oc(["config", "set", `plugins.entries.${pluginId}.config.configPath`, ovConfPath]);
    await oc(["config", "set", `plugins.entries.${pluginId}.config.port`, String(effectiveRuntimeConfig.port || DEFAULT_SERVER_PORT)]);
  } else {
    await oc(["config", "set", `plugins.entries.${pluginId}.config.mode`, "remote"]);
    await oc(["config", "set", `plugins.entries.${pluginId}.config.baseUrl`, effectiveRuntimeConfig.baseUrl || remoteBaseUrl]);
    if (effectiveRuntimeConfig.apiKey) {
      await oc(["config", "set", `plugins.entries.${pluginId}.config.apiKey`, effectiveRuntimeConfig.apiKey]);
    }
    if (effectiveRuntimeConfig.agentId) {
      await oc(["config", "set", `plugins.entries.${pluginId}.config.agentId`, effectiveRuntimeConfig.agentId]);
    }
  }

  // Legacy (memory) plugins need explicit targetUri/autoRecall/autoCapture (new version has defaults in config.ts)
  if (resolvedPluginKind === "memory") {
    await oc(["config", "set", `plugins.entries.${pluginId}.config.targetUri`, "viking://user/memories"]);
    await oc(["config", "set", `plugins.entries.${pluginId}.config.autoRecall`, "true", "--json"]);
    await oc(["config", "set", `plugins.entries.${pluginId}.config.autoCapture`, "true", "--json"]);
  }

  info(tr("OpenClaw plugin configured", "OpenClaw 插件配置完成"));
}

async function discoverOpenvikingPython(failedPy) {
  const candidates = IS_WIN
    ? ["python3", "python", "py -3"]
    : ["python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"];
  for (const candidate of candidates) {
    if (candidate === failedPy) continue;
    const resolved = await resolveAbsoluteCommand(candidate);
    if (!resolved || resolved === candidate || resolved === failedPy) continue;
    const check = await runCapture(resolved, ["-c", "import openviking"], { shell: false });
    if (check.code === 0) return resolved;
  }
  return "";
}

async function resolvePythonPath() {
  if (openvikingPythonPath) return openvikingPythonPath;
  const python = await checkPython();
  return python.cmd || "";
}

async function writeOpenvikingEnv({ includePython }) {
  const needStateDir = OPENCLAW_DIR !== DEFAULT_OPENCLAW_DIR;
  let pythonPath = "";
  if (includePython) {
    pythonPath = await resolvePythonPath();
    if (!pythonPath) {
      pythonPath = (process.env.OPENVIKING_PYTHON || "").trim() || (IS_WIN ? "python" : "python3");
      warn(
        tr(
          "Could not resolve absolute Python path; wrote fallback OPENVIKING_PYTHON to openviking.env. Edit that file if OpenViking fails to start.",
          "未能解析 Python 绝对路径，已在 openviking.env 中写入后备值。若启动失败请手动修改为虚拟环境中的 python 可执行文件路径。",
        ),
      );
    }

    // Verify the resolved Python can actually import openviking
    if (pythonPath) {
      const verify = await runCapture(pythonPath, ["-c", "import openviking"], { shell: false });
      if (verify.code !== 0) {
        warn(
          tr(
            `Resolved Python (${pythonPath}) cannot import openviking. The pip install target may differ from the runtime python3.`,
            `解析到的 Python（${pythonPath}）无法 import openviking。pip 安装目标可能与运行时的 python3 不一致。`,
          ),
        );
        // Try to discover the correct Python via pip show
        const corrected = await discoverOpenvikingPython(pythonPath);
        if (corrected) {
          info(
            tr(
              `Auto-corrected OPENVIKING_PYTHON to ${corrected}`,
              `已自动修正 OPENVIKING_PYTHON 为 ${corrected}`,
            ),
          );
          pythonPath = corrected;
        } else {
          warn(
            tr(
              `Could not auto-detect the correct Python. Edit OPENVIKING_PYTHON in the env file manually.`,
              `无法自动检测正确的 Python。请手动修改 env 文件中的 OPENVIKING_PYTHON。`,
            ),
          );
        }
      }
    }
  }

  // Remote mode + default state dir + no python line → nothing to persist
  if (!needStateDir && !pythonPath) return null;

  await mkdir(OPENCLAW_DIR, { recursive: true });

  if (IS_WIN) {
    const batLines = ["@echo off"];
    const psLines = [];

    if (needStateDir) {
      batLines.push(`set "OPENCLAW_STATE_DIR=${OPENCLAW_DIR.replace(/"/g, '""')}"`);
      psLines.push(`$env:OPENCLAW_STATE_DIR = "${OPENCLAW_DIR.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`);
    }
    if (pythonPath) {
      batLines.push(`set "OPENVIKING_PYTHON=${pythonPath.replace(/"/g, '""')}"`);
      psLines.push(`$env:OPENVIKING_PYTHON = "${pythonPath.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`);
    }

    const batPath = join(OPENCLAW_DIR, "openviking.env.bat");
    const ps1Path = join(OPENCLAW_DIR, "openviking.env.ps1");
    await writeFile(batPath, `${batLines.join("\r\n")}\r\n`, "utf8");
    await writeFile(ps1Path, `${psLines.join("\n")}\n`, "utf8");

    info(tr(`Environment file generated: ${batPath}`, `已生成环境文件: ${batPath}`));
    return { shellPath: batPath, powershellPath: ps1Path };
  }

  const lines = [];
  if (needStateDir) {
    lines.push(`export OPENCLAW_STATE_DIR='${OPENCLAW_DIR.replace(/'/g, "'\"'\"'")}'`);
  }
  if (pythonPath) {
    lines.push(`export OPENVIKING_PYTHON='${pythonPath.replace(/'/g, "'\"'\"'")}'`);
  }

  const envPath = join(OPENCLAW_DIR, "openviking.env");
  await writeFile(envPath, `${lines.join("\n")}\n`, "utf8");
  info(tr(`Environment file generated: ${envPath}`, `已生成环境文件: ${envPath}`));
  return { shellPath: envPath };
}

function wrapCommand(command, envFiles) {
  if (!envFiles) return command;
  if (IS_WIN) return `call "${envFiles.shellPath}" && ${command}`;
  return `source '${envFiles.shellPath.replace(/'/g, "'\"'\"'")}' && ${command}`;
}

function getExistingEnvFiles() {
  if (IS_WIN) {
    const batPath = join(OPENCLAW_DIR, "openviking.env.bat");
    const ps1Path = join(OPENCLAW_DIR, "openviking.env.ps1");
    if (existsSync(batPath)) {
      return { shellPath: batPath, powershellPath: existsSync(ps1Path) ? ps1Path : undefined };
    }
    if (existsSync(ps1Path)) {
      return { shellPath: ps1Path, powershellPath: ps1Path };
    }
    return null;
  }

  const envPath = join(OPENCLAW_DIR, "openviking.env");
  return existsSync(envPath) ? { shellPath: envPath } : null;
}

function ensureExistingPluginForUpgrade() {
  if (!existsSync(PLUGIN_DEST)) {
    err(
      tr(
        `Plugin upgrade mode expects an existing plugin at ${PLUGIN_DEST}. Run the full installer first if this is a fresh install.`,
        `插件升级模式要求 ${PLUGIN_DEST} 处已存在插件安装。若是首次安装，请先运行完整安装流程。`,
      ),
    );
    process.exit(1);
  }
}

async function main() {
  console.log("");
  bold(tr("🦣 OpenClaw + OpenViking Installer", "🦣 OpenClaw + OpenViking 一键安装"));
  console.log("");

  ensurePluginOnlyOperationArgs();
  await selectWorkdir();
  if (rollbackLastUpgrade) {
    info(tr("Mode: rollback last plugin upgrade", "模式: 回滚最近一次插件升级"));
    if (PLUGIN_VERSION !== "main") {
      warn("--plugin-version is ignored in --rollback mode.");
    }
    await rollbackLastUpgradeOperation();
    return;
  }
  validateRequestedPluginVersion();
  info(tr(`Target: ${OPENCLAW_DIR}`, `目标实例: ${OPENCLAW_DIR}`));
  info(tr(`Repository: ${REPO}`, `仓库: ${REPO}`));
  info(tr(`Plugin version: ${PLUGIN_VERSION}`, `插件版本: ${PLUGIN_VERSION}`));
  if (openvikingVersion) {
    info(tr(`OpenViking version: ${openvikingVersion}`, `OpenViking 版本: ${openvikingVersion}`));
  }

  if (upgradePluginOnly) {
    selectedMode = "local";
    info("Mode: plugin upgrade only (backup old plugin, clean only OpenViking plugin config, keep ov.conf)");
  } else {
    await selectMode();
  }
  info(tr(`Mode: ${selectedMode}`, `模式: ${selectedMode}`));

  if (upgradePluginOnly) {
    await checkOpenClaw();
    await resolvePluginConfig();
    await checkOpenClawCompatibility();
    await prepareStrongPluginUpgrade();
  } else if (selectedMode === "local") {
    await validateEnvironment();
    await checkOpenClaw();
    // Resolve plugin config after OpenClaw is available (for version detection)
    await resolvePluginConfig();
    await checkOpenClawCompatibility();
    checkRequestedOpenVikingCompatibility();
    await installOpenViking();
    await configureOvConf();
  } else {
    await checkOpenClaw();
    await resolvePluginConfig();
    await checkOpenClawCompatibility();
    await collectRemoteConfig();
  }

  let pluginPath;
  const localPluginDir = openvikingRepo ? join(openvikingRepo, "examples", resolvedPluginDir || "openclaw-plugin") : "";
  if (openvikingRepo && existsSync(join(localPluginDir, "index.ts"))) {
    pluginPath = localPluginDir;
    PLUGIN_DEST = join(OPENCLAW_DIR, "extensions", resolvedPluginId || "openviking");
    info(tr(`Using local plugin from repo: ${pluginPath}`, `使用仓库内插件: ${pluginPath}`));
    await deployPluginFromLocal(pluginPath);
      info(tr("Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
    pluginPath = PLUGIN_DEST;
  } else {
    await deployPluginFromRemote();
    pluginPath = PLUGIN_DEST;
  }

  await configureOpenClawPlugin(
    upgradePluginOnly
      ? {
          runtimeConfig: upgradeRuntimeConfig,
          skipGatewayMode: true,
          claimSlot: installedUpgradeState ? shouldClaimTargetSlot(installedUpgradeState) : true,
        }
      : { preserveExistingConfig: false },
  );
  await writeInstallStateFile({
    operation: upgradePluginOnly ? "upgrade" : "install",
    fromVersion: upgradeAudit?.fromVersion || "",
    configBackupPath: upgradeAudit?.configBackupPath || "",
    pluginBackups: upgradeAudit?.pluginBackups || [],
  });
  if (upgradeAudit) {
    upgradeAudit.completedAt = new Date().toISOString();
    await writeUpgradeAuditFile(upgradeAudit);
  }
  let envFiles = getExistingEnvFiles();
  if (!upgradePluginOnly) {
    envFiles = await writeOpenvikingEnv({
      includePython: selectedMode === "local",
    });
  } else if (!envFiles && OPENCLAW_DIR !== DEFAULT_OPENCLAW_DIR) {
    envFiles = await writeOpenvikingEnv({ includePython: false });
  }

  console.log("");
  bold("═══════════════════════════════════════════════════════════");
  bold(`  ${tr("Installation complete!", "安装完成！")}`);
  bold("═══════════════════════════════════════════════════════════");
  console.log("");

  if (upgradeAudit) {
    info(tr(`Upgrade path recorded: ${upgradeAudit.fromVersion} -> ${upgradeAudit.toVersion}`, `已记录升级路径: ${upgradeAudit.fromVersion} -> ${upgradeAudit.toVersion}`));
    info(tr(`Rollback config backup: ${upgradeAudit.configBackupPath}`, `回滚配置备份: ${upgradeAudit.configBackupPath}`));
    for (const pluginBackup of upgradeAudit.pluginBackups || []) {
      info(tr(`Rollback plugin backup: ${pluginBackup.backupDir}`, `回滚插件备份: ${pluginBackup.backupDir}`));
    }
    info(tr(`Rollback audit file: ${getUpgradeAuditPath()}`, `回滚审计文件: ${getUpgradeAuditPath()}`));
    console.log("");
  }

  if (selectedMode === "local") {
    info(tr("Run these commands to start OpenClaw + OpenViking:", "请按以下命令启动 OpenClaw + OpenViking："));
  } else {
    info(tr("Run these commands to start OpenClaw:", "请按以下命令启动 OpenClaw："));
  }
  console.log(`  1) ${wrapCommand("openclaw --version", envFiles)}`);
  console.log(`  2) ${wrapCommand("openclaw onboard", envFiles)}`);
  console.log(`  3) ${wrapCommand("openclaw gateway", envFiles)}`);
  console.log(`  4) ${wrapCommand("openclaw status", envFiles)}`);
  console.log("");

  if (selectedMode === "local") {
    if (envFiles?.shellPath && !IS_WIN) {
      info(
        tr(
          'If source fails, set: export OPENVIKING_PYTHON="$(command -v python3)"',
          '若 source 失败，可执行: export OPENVIKING_PYTHON="$(command -v python3)"',
        ),
      );
    }
    info(tr(`You can edit the config freely: ${OPENVIKING_DIR}/ov.conf`, `你可以按需自由修改配置文件: ${OPENVIKING_DIR}/ov.conf`));
  } else {
    info(tr(`Remote server: ${remoteBaseUrl}`, `远程服务器: ${remoteBaseUrl}`));
  }
  console.log("");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
