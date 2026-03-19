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
 *   node install.js [ -y | --yes ] [ --zh ] [ --workdir PATH ]
 *                   [ --openviking-version=V ] [ --repo=PATH ]
 *
 * Environment variables:
 *   REPO, BRANCH, OPENVIKING_INSTALL_YES, SKIP_OPENCLAW, SKIP_OPENVIKING
 *   OPENVIKING_VERSION       Pip install openviking==VERSION (omit for latest)
 *   OPENVIKING_REPO          Repo path: source install (pip -e) + local plugin (default: off)
 *   NPM_REGISTRY, PIP_INDEX_URL
 *   OPENVIKING_VLM_API_KEY, OPENVIKING_EMBEDDING_API_KEY, OPENVIKING_ARK_API_KEY
 *   OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES (Linux)
 */

import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const REPO = process.env.REPO || "volcengine/OpenViking";
const BRANCH = process.env.BRANCH || "main";
const GH_RAW = `https://raw.githubusercontent.com/${REPO}/${BRANCH}`;
const NPM_REGISTRY = process.env.NPM_REGISTRY || "https://registry.npmmirror.com";
const PIP_INDEX_URL = process.env.PIP_INDEX_URL || "https://pypi.tuna.tsinghua.edu.cn/simple";

const IS_WIN = process.platform === "win32";
const HOME = process.env.HOME || process.env.USERPROFILE || "";

const DEFAULT_OPENCLAW_DIR = join(HOME, ".openclaw");
let OPENCLAW_DIR = DEFAULT_OPENCLAW_DIR;
let PLUGIN_DEST = join(OPENCLAW_DIR, "extensions", "openviking");

const OPENVIKING_DIR = join(HOME, ".openviking");

const DEFAULT_SERVER_PORT = 1933;
const DEFAULT_AGFS_PORT = 1833;
const DEFAULT_VLM_MODEL = "doubao-seed-2-0-pro-260215";
const DEFAULT_EMBED_MODEL = "doubao-embedding-vision-251215";

const REQUIRED_PLUGIN_FILES = [
  "examples/openclaw-plugin/index.ts",
  "examples/openclaw-plugin/context-engine.ts",
  "examples/openclaw-plugin/config.ts",
  "examples/openclaw-plugin/openclaw.plugin.json",
  "examples/openclaw-plugin/package.json",
  "examples/openclaw-plugin/package-lock.json",
  "examples/openclaw-plugin/.gitignore",
];

const OPTIONAL_PLUGIN_FILES = [
  "examples/openclaw-plugin/client.ts",
  "examples/openclaw-plugin/process-manager.ts",
  "examples/openclaw-plugin/memory-ranking.ts",
  "examples/openclaw-plugin/text-utils.ts",
];

let installYes = process.env.OPENVIKING_INSTALL_YES === "1";
let langZh = false;
let openvikingVersion = process.env.OPENVIKING_VERSION || "";
let openvikingRepo = process.env.OPENVIKING_REPO || "";
let workdirExplicit = false;

let selectedMode = "local";
let selectedServerPort = DEFAULT_SERVER_PORT;
let remoteBaseUrl = "http://127.0.0.1:1933";
let remoteApiKey = "";
let remoteAgentId = "";
let openvikingPythonPath = "";

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
  if (arg.startsWith("--openviking-version=")) {
    openvikingVersion = arg.slice("--openviking-version=".length).trim();
    continue;
  }
  if (arg.startsWith("--repo=")) {
    openvikingRepo = arg.slice("--repo=".length).trim();
    continue;
  }
  if (arg === "-h" || arg === "--help") {
    printHelp();
    process.exit(0);
  }
}

const OPENVIKING_PIP_SPEC = openvikingVersion ? `openviking==${openvikingVersion}` : "openviking";

function setOpenClawDir(dir) {
  OPENCLAW_DIR = dir;
  PLUGIN_DEST = join(OPENCLAW_DIR, "extensions", "openviking");
}

function printHelp() {
  console.log("Usage: node install.js [ -y | --yes ] [ --zh ] [ --workdir PATH ] [ --openviking-version=V ] [ --repo=PATH ]");
  console.log("");
  console.log("  -y, --yes   Non-interactive (use defaults)");
  console.log("  --zh        Chinese prompts");
  console.log("  --workdir   OpenClaw config directory (default: ~/.openclaw)");
  console.log("  --openviking-version=VERSION   Pip install openviking==VERSION (default: latest)");
  console.log("  --repo=PATH   Use OpenViking repo at PATH: pip install -e PATH, plugin from repo (default: off)");
  console.log("  -h, --help  This help");
  console.log("");
  console.log("Env: OPENVIKING_REPO, REPO, BRANCH, SKIP_OPENCLAW, SKIP_OPENVIKING, OPENVIKING_VERSION, NPM_REGISTRY, PIP_INDEX_URL");
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

async function checkPython() {
  const py = process.env.OPENVIKING_PYTHON || (IS_WIN ? "python" : "python3");
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

  const py = python.cmd;

  if (openvikingRepo && existsSync(join(openvikingRepo, "pyproject.toml"))) {
    info(tr(`Installing OpenViking from source (editable): ${openvikingRepo}`, `正在从源码安装 OpenViking（可编辑）: ${openvikingRepo}`));
    await run(py, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", PIP_INDEX_URL], { silent: true });
    await run(py, ["-m", "pip", "install", "-e", openvikingRepo]);
    openvikingPythonPath = py;
    info(tr("OpenViking installed ✓ (source)", "OpenViking 安装完成 ✓（源码）"));
    return;
  }

  info(tr("Installing OpenViking from PyPI...", "正在安装 OpenViking (PyPI)..."));
  info(tr(`Using pip index: ${PIP_INDEX_URL}`, `使用 pip 镜像源: ${PIP_INDEX_URL}`));

  info(`Package: ${OPENVIKING_PIP_SPEC}`);
  await runCapture(py, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", PIP_INDEX_URL], { shell: false });
  const installResult = await runLiveCapture(
    py,
    ["-m", "pip", "install", "--progress-bar", "on", OPENVIKING_PIP_SPEC, "-i", PIP_INDEX_URL],
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
          ["-m", "pip", "install", "--progress-bar", "on", "-U", OPENVIKING_PIP_SPEC, "-i", PIP_INDEX_URL],
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
      ["-m", "pip", "install", "--progress-bar", "on", OPENVIKING_PIP_SPEC, "-i", PIP_INDEX_URL],
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
      ["-m", "pip", "install", "--progress-bar", "on", "--break-system-packages", OPENVIKING_PIP_SPEC, "-i", PIP_INDEX_URL],
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
        backend: "volcengine",
        api_key: embeddingApiKey || null,
        model: embeddingModel,
        api_base: "https://ark.cn-beijing.volces.com/api/v3",
        dimension: 1024,
        input: "multimodal",
      },
    },
    vlm: {
      backend: "volcengine",
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

async function downloadPluginFile(relPath, required, index, total) {
  const fileName = relPath.split("/").pop();
  const url = `${GH_RAW}/${relPath}`;
  const maxRetries = 3;

  process.stdout.write(`  [${index}/${total}] ${fileName} `);

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        const buffer = Buffer.from(await response.arrayBuffer());
        await writeFile(join(PLUGIN_DEST, fileName), buffer);
        console.log("✓");
        return;
      }
      if (!required && response.status === 404) {
        console.log(tr("(not present in target branch, skipped)", "（目标分支不存在，已跳过）"));
        return;
      }
    } catch {}

    if (attempt < maxRetries) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }

  if (fileName === ".gitignore") {
    console.log(tr("(retries failed, using minimal .gitignore)", "（重试失败，使用最小 .gitignore）"));
    await writeFile(join(PLUGIN_DEST, fileName), "node_modules/\n", "utf8");
    return;
  }

  console.log("");
  err(tr(`Download failed: ${url}`, `下载失败: ${url}`));
  process.exit(1);
}

async function downloadPlugin() {
  await mkdir(PLUGIN_DEST, { recursive: true });
  const files = [
    ...REQUIRED_PLUGIN_FILES.map((relPath) => ({ relPath, required: true })),
    ...OPTIONAL_PLUGIN_FILES.map((relPath) => ({ relPath, required: false })),
  ];

  info(tr(`Downloading openviking plugin from ${REPO}@${BRANCH}...`, `正在从 ${REPO}@${BRANCH} 下载 openviking 插件...`));
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    await downloadPluginFile(file.relPath, file.required, i + 1, files.length);
  }

  info(tr("Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
  await run("npm", ["install", "--no-audit", "--no-fund"], { cwd: PLUGIN_DEST, silent: false });
  info(tr(`Plugin deployed: ${PLUGIN_DEST}`, `插件部署完成: ${PLUGIN_DEST}`));
}

async function configureOpenClawPlugin(pluginPath = PLUGIN_DEST) {
  info(tr("Configuring OpenClaw plugin...", "正在配置 OpenClaw 插件..."));

  const ocEnv = { ...process.env };
  if (OPENCLAW_DIR !== DEFAULT_OPENCLAW_DIR) {
    ocEnv.OPENCLAW_STATE_DIR = OPENCLAW_DIR;
  }

  const oc = (args) => runCapture("openclaw", args, { env: ocEnv, shell: IS_WIN });

  // Enable plugin (files already deployed to extensions dir by deployPlugin)
  const enableResult = await oc(["plugins", "enable", "openviking"]);
  if (enableResult.code !== 0) throw new Error(`openclaw plugins enable failed (exit code ${enableResult.code})`);
  await oc(["config", "set", "plugins.slots.contextEngine", "openviking"]);

  // Set gateway mode
  await oc(["config", "set", "gateway.mode", "local"]);

  // Set plugin config for the selected mode
  if (selectedMode === "local") {
    const ovConfPath = join(OPENVIKING_DIR, "ov.conf");
    await oc(["config", "set", "plugins.entries.openviking.config.mode", "local"]);
    await oc(["config", "set", "plugins.entries.openviking.config.configPath", ovConfPath]);
    await oc(["config", "set", "plugins.entries.openviking.config.port", String(selectedServerPort)]);
  } else {
    await oc(["config", "set", "plugins.entries.openviking.config.mode", "remote"]);
    await oc(["config", "set", "plugins.entries.openviking.config.baseUrl", remoteBaseUrl]);
    if (remoteApiKey) {
      await oc(["config", "set", "plugins.entries.openviking.config.apiKey", remoteApiKey]);
    }
    if (remoteAgentId) {
      await oc(["config", "set", "plugins.entries.openviking.config.agentId", remoteAgentId]);
    }
  }

  info(tr("OpenClaw plugin configured", "OpenClaw 插件配置完成"));
}

async function resolvePythonPath() {
  if (openvikingPythonPath) return openvikingPythonPath;
  const python = await checkPython();
  const py = python.cmd;
  if (!py) return "";

  if (IS_WIN) {
    const result = await runCapture("where", [py], { shell: true });
    return result.out.split(/\r?\n/)[0]?.trim() || py;
  }

  const result = await runCapture("which", [py], { shell: false });
  return result.out.trim() || py;
}

async function writeOpenvikingEnv({ includePython }) {
  const needStateDir = OPENCLAW_DIR !== DEFAULT_OPENCLAW_DIR;
  const pythonPath = includePython ? await resolvePythonPath() : "";
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

async function main() {
  console.log("");
  bold(tr("🦣 OpenClaw + OpenViking Installer", "🦣 OpenClaw + OpenViking 一键安装"));
  console.log("");

  await selectWorkdir();
  info(tr(`Target: ${OPENCLAW_DIR}`, `目标实例: ${OPENCLAW_DIR}`));

  await selectMode();
  info(tr(`Mode: ${selectedMode}`, `模式: ${selectedMode}`));

  if (selectedMode === "local") {
    await validateEnvironment();
    await checkOpenClaw();
    await installOpenViking();
    await configureOvConf();
  } else {
    await checkOpenClaw();
    await collectRemoteConfig();
  }

  let pluginPath;
  const localPluginDir = openvikingRepo ? join(openvikingRepo, "examples", "openclaw-plugin") : "";
  if (openvikingRepo && existsSync(join(localPluginDir, "index.ts"))) {
    pluginPath = localPluginDir;
    info(tr(`Using local plugin from repo: ${pluginPath}`, `使用仓库内插件: ${pluginPath}`));
    if (!existsSync(join(pluginPath, "node_modules"))) {
      info(tr("Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
      await run("npm", ["install", "--no-audit", "--no-fund"], { cwd: pluginPath, silent: false });
    }
  } else {
    await downloadPlugin();
    pluginPath = PLUGIN_DEST;
  }

  await configureOpenClawPlugin(pluginPath);
  const envFiles = await writeOpenvikingEnv({
    includePython: selectedMode === "local",
  });

  console.log("");
  bold("═══════════════════════════════════════════════════════════");
  bold(`  ${tr("Installation complete!", "安装完成！")}`);
  bold("═══════════════════════════════════════════════════════════");
  console.log("");

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
