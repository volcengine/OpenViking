#!/usr/bin/env node
/**
 * OpenClaw OpenViking plugin installer (remote OpenViking server — does not install Python/OpenViking server).
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
 *                   [ --plugin-version=TAG ]
 *
 * Environment variables:
 *   REPO, PLUGIN_VERSION (or BRANCH), OPENVIKING_INSTALL_YES, SKIP_OPENCLAW
 *   NPM_REGISTRY
 */

import { spawn } from "node:child_process";
import { cp, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { existsSync, readdirSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

let REPO = process.env.REPO || "volcengine/OpenViking";
// PLUGIN_VERSION takes precedence over BRANCH (legacy). If omitted, resolve the latest tag from GitHub.
const pluginVersionEnv = (process.env.PLUGIN_VERSION || process.env.BRANCH || "").trim();
let PLUGIN_VERSION = pluginVersionEnv;
let pluginVersionExplicit = Boolean(pluginVersionEnv);
const NPM_REGISTRY = process.env.NPM_REGISTRY || "https://registry.npmmirror.com";

const IS_WIN = process.platform === "win32";
const HOME = process.env.HOME || process.env.USERPROFILE || "";

const DEFAULT_OPENCLAW_DIR = join(HOME, ".openclaw");
let OPENCLAW_DIR = DEFAULT_OPENCLAW_DIR;
let PLUGIN_DEST = "";  // Will be set after resolving plugin config

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
  required: ["index.ts", "config.ts", "package.json", "openclaw.plugin.json"],
  optional: [
    "context-engine.ts",
    "client.ts",
    "process-manager.ts",
    "memory-ranking.ts",
    "text-utils.ts",
    "tool-call-id.ts",
    "session-transcript-repair.ts",
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
let workdirExplicit = false;
let upgradePluginOnly = false;
let rollbackLastUpgrade = false;
let showCurrentVersion = false;

const selectedMode = "remote";
let remoteBaseUrl = "http://127.0.0.1:1933";
let remoteApiKey = "";
let remoteAgentPrefix = "";
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
  if (arg === "--current-version") {
    showCurrentVersion = true;
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
    const version = arg.slice("--plugin-version=".length).trim();
    if (!version) {
      console.error("--plugin-version requires a value");
      process.exit(1);
    }
    PLUGIN_VERSION = version;
    pluginVersionExplicit = true;
    continue;
  }
  if (arg === "--plugin-version") {
    const version = argv[i + 1]?.trim();
    if (!version) {
      console.error("--plugin-version requires a value");
      process.exit(1);
    }
    PLUGIN_VERSION = version;
    pluginVersionExplicit = true;
    i += 1;
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
  console.log("  --plugin-version=TAG     Plugin version (Git tag, e.g. v0.2.9, default: latest tag)");
  console.log("  --workdir PATH           OpenClaw config directory (default: ~/.openclaw)");
  console.log("  --current-version        Print installed plugin version and exit");
  console.log("  --update, --upgrade-plugin");
  console.log("                           Upgrade only the plugin to the requested --plugin-version; keeps existing plugin runtime config");
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
  console.log("  # Show installed versions");
  console.log("  node install.js --current-version");
  console.log("");
  console.log("  # Install a specific release version");
  console.log("  node install.js --plugin-version=v0.2.9");
  console.log("");
  console.log("  # Install from a fork repository");
  console.log("  node install.js --github-repo=yourname/OpenViking --plugin-version=dev-branch");
  console.log("");
  console.log("  # Install specific plugin version");
  console.log("  node install.js --plugin-version=v0.2.8");
  console.log("");
  console.log("  # Upgrade only the plugin files from main branch");
  console.log("  node install.js --update --plugin-version=main");
  console.log("");
  console.log("  # Roll back the last plugin upgrade");
  console.log("  node install.js --rollback");
  console.log("");
  console.log("Env: REPO, PLUGIN_VERSION, SKIP_OPENCLAW, NPM_REGISTRY");
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

function isValidAgentPrefixInput(value) {
  const trimmed = String(value || "").trim();
  return !trimmed || /^[a-zA-Z0-9_-]+$/.test(trimmed);
}

async function questionAgentPrefix(defaultValue = "") {
  while (true) {
    const answer = (await question(
      tr("Agent Prefix (optional)", "Agent Prefix（可选）"),
      defaultValue,
    )).trim();
    if (isValidAgentPrefixInput(answer)) {
      return answer;
    }
    warn(tr(
      "Agent Prefix may only contain letters, digits, underscores, and hyphens, or be empty.",
      "Agent Prefix 只能包含字母、数字、下划线和连字符，或留空。",
    ));
  }
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
  if (showCurrentVersion) {
    setOpenClawDir(instances[0]);
    return;
  }
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

async function collectRemoteConfig() {
  if (installYes) return;
  remoteBaseUrl = await question(tr("OpenViking server URL", "OpenViking 服务器地址"), remoteBaseUrl);
  remoteApiKey = await question(tr("API Key (optional)", "API Key（可选）"), remoteApiKey);
  remoteAgentPrefix = await questionAgentPrefix(remoteAgentPrefix);
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

function compareSemverDesc(a, b) {
  if (versionGte(a, b) && versionGte(b, a)) {
    return 0;
  }
  return versionGte(a, b) ? -1 : 1;
}

function pickLatestPluginTag(tagNames) {
  const normalized = tagNames
    .map((tag) => String(tag ?? "").trim())
    .filter(Boolean);

  const semverTags = normalized
    .filter((tag) => isSemverLike(tag))
    .sort(compareSemverDesc);

  if (semverTags.length > 0) {
    return semverTags[0];
  }

  return normalized[0] || "";
}

function parseGitLsRemoteTags(output) {
  return String(output ?? "")
    .split(/\r?\n/)
    .map((line) => {
      const match = line.match(/refs\/tags\/(.+)$/);
      return match?.[1]?.trim() || "";
    })
    .filter(Boolean);
}

async function resolveDefaultPluginVersion() {
  if (PLUGIN_VERSION) {
    return;
  }

  info(tr(
    `No plugin version specified; resolving latest tag from ${REPO}...`,
    `未指定插件版本，正在解析 ${REPO} 的最新 tag...`,
  ));

  const failures = [];
  const apiUrl = `https://api.github.com/repos/${REPO}/tags?per_page=100`;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    const response = await fetch(apiUrl, {
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": "openviking-setup-helper",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (response.ok) {
      const payload = await response.json().catch(() => null);
      if (Array.isArray(payload)) {
        const latestTag = pickLatestPluginTag(payload.map((item) => item?.name || ""));
        if (latestTag) {
          PLUGIN_VERSION = latestTag;
          info(tr(
            `Resolved default plugin version to latest tag: ${PLUGIN_VERSION}`,
            `已将默认插件版本解析为最新 tag: ${PLUGIN_VERSION}`,
          ));
          return;
        }
      } else {
        failures.push("GitHub tags API returned an unexpected payload");
      }
    } else {
      failures.push(`GitHub tags API returned HTTP ${response.status}`);
    }
  } catch (error) {
    failures.push(`GitHub tags API failed: ${String(error)}`);
  }

  const gitRef = `https://github.com/${REPO}.git`;
  const gitResult = await runCapture("git", ["ls-remote", "--tags", "--refs", gitRef], {
    shell: IS_WIN,
  });
  if (gitResult.code === 0 && gitResult.out) {
    const latestTag = pickLatestPluginTag(parseGitLsRemoteTags(gitResult.out));
    if (latestTag) {
      PLUGIN_VERSION = latestTag;
      info(tr(
        `Resolved default plugin version via git tags: ${PLUGIN_VERSION}`,
        `已通过 git tag 解析默认插件版本: ${PLUGIN_VERSION}`,
      ));
      return;
    }
    failures.push("git ls-remote returned no usable tags");
  } else {
    failures.push(`git ls-remote failed${gitResult.err ? `: ${gitResult.err}` : ""}`);
  }

  err(tr(
    `Could not resolve the latest tag for ${REPO}.`,
    `无法解析 ${REPO} 的最新 tag。`,
  ));
  console.log(tr(
    "Please rerun with --plugin-version <tag>, or use --plugin-version main to track the branch head explicitly.",
    "请使用 --plugin-version <tag> 重新执行；如果需要显式跟踪分支头，请使用 --plugin-version main。",
  ));
  if (failures.length > 0) {
    warn(failures.join(" | "));
  }
  process.exit(1);
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
  if (isSemverLike(PLUGIN_VERSION) && !versionGte(PLUGIN_VERSION, "v0.2.8")) {
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

async function printCurrentVersionInfo() {
  const state = await readJsonFileIfExists(getInstallStatePathForPlugin("openviking"));
  const pluginRequestedRef = state?.requestedRef || "";
  const pluginReleaseId = state?.releaseId || "";
  const pluginInstalledAt = state?.installedAt || "";

  console.log("");
  bold(tr("Installed versions", "当前已安装版本"));
  console.log("");
  console.log(`Target: ${OPENCLAW_DIR}`);
  console.log(`Plugin: ${pluginReleaseId || pluginRequestedRef || "not installed"}`);
  if (pluginRequestedRef && pluginReleaseId && pluginRequestedRef !== pluginReleaseId) {
    console.log(`Plugin requested ref: ${pluginRequestedRef}`);
  }
  console.log(tr("OpenViking server: not installed by this tool (use a remote URL in plugin config)", "OpenViking 服务端：本工具不安装；请在插件配置中填写远程服务地址"));
  if (pluginInstalledAt) {
    console.log(`Installed at: ${pluginInstalledAt}`);
  }
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

  const runtime = {};
  if (typeof entryConfig.baseUrl === "string" && entryConfig.baseUrl.trim()) {
    runtime.baseUrl = entryConfig.baseUrl.trim();
  }
  if (typeof entryConfig.apiKey === "string" && entryConfig.apiKey.trim()) {
    runtime.apiKey = entryConfig.apiKey;
  }
  if (typeof entryConfig.agent_prefix === "string" && entryConfig.agent_prefix.trim()) {
    runtime.agent_prefix = entryConfig.agent_prefix.trim();
  }
  return runtime;
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

function prepareUpgradeRuntimeConfig(installedState) {
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
    runtime = {};
  }

  delete runtime.mode;
  runtime.baseUrl = runtime.baseUrl || remoteBaseUrl;
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
  upgradeRuntimeConfig = prepareUpgradeRuntimeConfig(installedState);
  const fromVersion = formatInstalledStateLabel(installedState);
  const toVersion = formatTargetVersionLabel();
  info(
    tr(
      `Detected installed OpenViking plugin state: ${installedState.generation}`,
      `检测到已安装 OpenViking 插件状态: ${installedState.generation}`,
    ),
  );
  remoteBaseUrl = upgradeRuntimeConfig.baseUrl || remoteBaseUrl;
  remoteApiKey = upgradeRuntimeConfig.apiKey || "";
  remoteAgentPrefix = upgradeRuntimeConfig.agent_prefix || "";
  info(tr(`Upgrade runtime mode: ${selectedMode} (remote OpenViking server)`, `升级运行模式: ${selectedMode}（远程 OpenViking 服务）`));

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
      "Upgrade will preserve existing plugin server connection settings where possible and re-apply minimal remote plugin config.",
      "升级将尽可能保留已有的插件服务端连接信息，并只回填最少的远程插件配置。",
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

async function createPluginStagingDir() {
  const pluginId = resolvedPluginId || "openviking";
  const extensionsDir = join(OPENCLAW_DIR, "extensions");
  await mkdir(extensionsDir, { recursive: true });
  const stagingPrefix = `.${pluginId}.staging-`;
  try {
    const entries = readdirSync(extensionsDir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory() && entry.name.startsWith(stagingPrefix)) {
        await rm(join(extensionsDir, entry.name), { recursive: true, force: true });
      }
    }
  } catch {}
  const stagingDir = join(extensionsDir, `${stagingPrefix}${process.pid}-${Date.now()}`);
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

async function configureOpenClawPlugin({
  preserveExistingConfig = false,
  runtimeConfig = null,
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

  const effectiveRuntimeConfig = runtimeConfig || {
    baseUrl: remoteBaseUrl,
    apiKey: remoteApiKey,
    agent_prefix: remoteAgentPrefix,
  };

  await oc(["config", "set", `plugins.entries.${pluginId}.config.baseUrl`, effectiveRuntimeConfig.baseUrl || remoteBaseUrl]);
  if (effectiveRuntimeConfig.apiKey) {
    await oc(["config", "set", `plugins.entries.${pluginId}.config.apiKey`, effectiveRuntimeConfig.apiKey]);
  }
  if (effectiveRuntimeConfig.agent_prefix) {
    await oc(["config", "set", `plugins.entries.${pluginId}.config.agent_prefix`, effectiveRuntimeConfig.agent_prefix]);
  }

  // Legacy (memory) plugins need explicit targetUri/autoRecall/autoCapture (new version has defaults in config.ts)
  if (resolvedPluginKind === "memory") {
    await oc(["config", "set", `plugins.entries.${pluginId}.config.targetUri`, "viking://user/memories"]);
    await oc(["config", "set", `plugins.entries.${pluginId}.config.autoRecall`, "true", "--json"]);
    await oc(["config", "set", `plugins.entries.${pluginId}.config.autoCapture`, "true", "--json"]);
  }

  info(tr("OpenClaw plugin configured", "OpenClaw 插件配置完成"));
}

async function writeOpenvikingEnv() {
  const needStateDir = OPENCLAW_DIR !== DEFAULT_OPENCLAW_DIR;
  if (!needStateDir) return null;

  await mkdir(OPENCLAW_DIR, { recursive: true });

  if (IS_WIN) {
    const batLines = ["@echo off"];
    const psLines = [];

    batLines.push(`set "OPENCLAW_STATE_DIR=${OPENCLAW_DIR.replace(/"/g, '""')}"`);
    psLines.push(`$env:OPENCLAW_STATE_DIR = "${OPENCLAW_DIR.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`);

    const batPath = join(OPENCLAW_DIR, "openviking.env.bat");
    const ps1Path = join(OPENCLAW_DIR, "openviking.env.ps1");
    await writeFile(batPath, `${batLines.join("\r\n")}\r\n`, "utf8");
    await writeFile(ps1Path, `${psLines.join("\n")}\n`, "utf8");

    info(tr(`Environment file generated: ${batPath}`, `已生成环境文件: ${batPath}`));
    return { shellPath: batPath, powershellPath: ps1Path };
  }

  const envPath = join(OPENCLAW_DIR, "openviking.env");
  await writeFile(
    envPath,
    `export OPENCLAW_STATE_DIR='${OPENCLAW_DIR.replace(/'/g, "'\"'\"'")}'\n`,
    "utf8",
  );
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

async function main() {
  console.log("");
  bold(tr("🦣 OpenClaw OpenViking plugin installer", "🦣 OpenClaw OpenViking 插件安装"));
  console.log("");

  await selectWorkdir();
  if (showCurrentVersion) {
    await printCurrentVersionInfo();
    return;
  }
  if (rollbackLastUpgrade) {
    info(tr("Mode: rollback last plugin upgrade", "模式: 回滚最近一次插件升级"));
    if (pluginVersionExplicit) {
      warn("--plugin-version is ignored in --rollback mode.");
    }
    await rollbackLastUpgradeOperation();
    return;
  }
  await resolveDefaultPluginVersion();
  validateRequestedPluginVersion();
  info(tr(`Target: ${OPENCLAW_DIR}`, `目标实例: ${OPENCLAW_DIR}`));
  info(tr(`Repository: ${REPO}`, `仓库: ${REPO}`));
  info(tr(`Plugin version: ${PLUGIN_VERSION}`, `插件版本: ${PLUGIN_VERSION}`));

  if (upgradePluginOnly) {
    info(tr("Mode: plugin upgrade only", "模式: 仅升级插件"));
  }
  info(tr(`Mode: ${selectedMode}`, `模式: ${selectedMode}`));

  if (upgradePluginOnly) {
    await checkOpenClaw();
    await resolvePluginConfig();
    await checkOpenClawCompatibility();
    await prepareStrongPluginUpgrade();
  } else {
    await checkOpenClaw();
    await resolvePluginConfig();
    await checkOpenClawCompatibility();
    await collectRemoteConfig();
  }

  await deployPluginFromRemote();

  await configureOpenClawPlugin(
    upgradePluginOnly
      ? {
          runtimeConfig: upgradeRuntimeConfig,
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
    envFiles = await writeOpenvikingEnv();
  } else if (!envFiles && OPENCLAW_DIR !== DEFAULT_OPENCLAW_DIR) {
    envFiles = await writeOpenvikingEnv();
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

  info(tr("Run these commands to start OpenClaw:", "请按以下命令启动 OpenClaw："));
  console.log(`  1) ${wrapCommand("openclaw --version", envFiles)}`);
  console.log(`  2) ${wrapCommand("openclaw onboard", envFiles)}`);
  console.log(`  3) ${wrapCommand("openclaw gateway", envFiles)}`);
  console.log(`  4) ${wrapCommand("openclaw status", envFiles)}`);
  console.log("");

  info(tr(`OpenViking server URL (plugin): ${remoteBaseUrl}`, `OpenViking 服务地址（插件）: ${remoteBaseUrl}`));
  console.log("");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
