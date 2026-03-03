#!/usr/bin/env node
/**
 * OpenClaw + OpenViking setup helper
 * Usage: npx openclaw-openviking-setup-helper
 * Or: npx openclaw-openviking-setup-helper --help
 */

import { spawn } from "node:child_process";
import { mkdir, writeFile, access, readFile, rm } from "node:fs/promises";
import { createInterface } from "node:readline";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const GITHUB_RAW =
  process.env.OPENVIKING_GITHUB_RAW ||
  "https://raw.githubusercontent.com/OpenViking/OpenViking/main";

const IS_WIN = process.platform === "win32";
const IS_LINUX = process.platform === "linux";
const HOME = process.env.HOME || process.env.USERPROFILE || "";
const OPENCLAW_DIR = join(HOME, ".openclaw");
const OPENVIKING_DIR = join(HOME, ".openviking");
const EXT_DIR = join(OPENCLAW_DIR, "extensions");
const PLUGIN_DEST = join(EXT_DIR, "memory-openviking");

// ─── Utility helpers ───

function log(msg, level = "info") {
  const icons = { info: "\u2139", ok: "\u2713", err: "\u2717", warn: "\u26A0" };
  console.log(`${icons[level] || ""} ${msg}`);
}

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, {
      stdio: opts.silent ? "pipe" : "inherit",
      shell: opts.shell ?? true,
      ...opts,
    });
    p.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`exit ${code}`))));
  });
}

function runCapture(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, {
      stdio: ["ignore", "pipe", "pipe"],
      shell: opts.shell ?? false,
      ...opts,
    });
    let out = "";
    let err = "";
    p.stdout?.on("data", (d) => (out += d));
    p.stderr?.on("data", (d) => (err += d));
    p.on("error", (e) => {
      if (e.code === "ENOENT") resolve({ code: -1, out: "", err: `command not found: ${cmd}` });
      else resolve({ code: -1, out: "", err: String(e) });
    });
    p.on("close", (code) => resolve({ code, out: out.trim(), err: err.trim() }));
  });
}

function runCaptureWithTimeout(cmd, args, timeoutMs, opts = {}) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, {
      stdio: ["ignore", "pipe", "pipe"],
      shell: opts.shell ?? false,
      ...opts,
    });
    let out = "";
    let err = "";
    let settled = false;
    const done = (result) => { if (!settled) { settled = true; resolve(result); } };
    const timer = setTimeout(() => { p.kill(); done({ code: out ? 0 : -1, out: out.trim(), err: err.trim() }); }, timeoutMs);
    p.stdout?.on("data", (d) => (out += d));
    p.stderr?.on("data", (d) => (err += d));
    p.on("error", (e) => { clearTimeout(timer); done({ code: -1, out: "", err: String(e) }); });
    p.on("close", (code) => { clearTimeout(timer); done({ code, out: out.trim(), err: err.trim() }); });
  });
}

async function question(prompt, defaultValue = "") {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const def = defaultValue ? ` [${defaultValue}]` : "";
  return new Promise((resolve) => {
    rl.question(`${prompt}${def}: `, (answer) => {
      rl.close();
      resolve((answer ?? defaultValue).trim() || defaultValue);
    });
  });
}

async function questionApiKey(prompt) {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(prompt, (answer) => {
      rl.close();
      resolve((answer ?? "").trim());
    });
  });
}

// ─── Distro detection ───

async function detectDistro() {
  if (IS_WIN) return "windows";
  try {
    const { out } = await runCapture("sh", ["-c", "cat /etc/os-release 2>/dev/null"]);
    const lower = out.toLowerCase();
    if (lower.includes("ubuntu") || lower.includes("debian")) return "debian";
    if (lower.includes("centos") || lower.includes("rhel") || lower.includes("openeuler") || lower.includes("fedora") || lower.includes("rocky") || lower.includes("alma")) return "rhel";
  } catch {}
  const { code: aptCode } = await runCapture("sh", ["-c", "command -v apt"]);
  if (aptCode === 0) return "debian";
  const { code: dnfCode } = await runCapture("sh", ["-c", "command -v dnf || command -v yum"]);
  if (dnfCode === 0) return "rhel";
  return "unknown";
}

// ─── Environment checks ───

const DEFAULT_PYTHON = IS_WIN ? "python" : "python3";

async function checkOpenclaw() {
  if (IS_WIN) {
    const { code } = await runCaptureWithTimeout("openclaw", ["--version"], 10000, { shell: true });
    return code === 0 ? { ok: true } : { ok: false };
  }
  const { code } = await runCapture("openclaw", ["--version"]);
  return code === 0 ? { ok: true } : { ok: false };
}

async function checkPython() {
  const py = process.env.OPENVIKING_PYTHON || DEFAULT_PYTHON;
  const { code, out } = await runCapture(py, ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]);
  if (code !== 0) return { ok: false, version: null, cmd: py, msg: `Python not found (tried: ${py})` };
  const [major, minor] = out.split(".").map(Number);
  if (major < 3 || (major === 3 && minor < 10))
    return { ok: false, version: out, cmd: py, msg: `Python ${out} too old, need >= 3.10` };
  return { ok: true, version: out, cmd: py, msg: `${out} (${py})` };
}

async function checkGo() {
  const goDir = process.env.OPENVIKING_GO_PATH?.replace(/^~/, HOME);
  const goCmd = goDir ? join(goDir, "go") : "go";
  const { code, out } = await runCapture(goCmd, ["version"]);
  if (code !== 0) return { ok: false, version: null, msg: "Go not found" };
  const m = out.match(/go([0-9]+)\.([0-9]+)/);
  if (!m) return { ok: false, version: null, msg: "Cannot parse Go version" };
  const [, major, minor] = m.map(Number);
  if (major < 1 || (major === 1 && minor < 25))
    return { ok: false, version: `${major}.${minor}`, msg: `Go ${major}.${minor} too old, need >= 1.25` };
  return { ok: true, version: `${major}.${minor}`, msg: `${major}.${minor}` };
}

async function checkCmake() {
  const { code } = await runCapture("cmake", ["--version"]);
  return { ok: code === 0 };
}

async function checkGpp() {
  const { code } = await runCapture("g++", ["--version"]);
  return { ok: code === 0 };
}

async function checkOpenvikingModule() {
  const py = process.env.OPENVIKING_PYTHON || DEFAULT_PYTHON;
  const { code } = await runCapture(py, ["-c", "import openviking"]);
  return code === 0 ? { ok: true } : { ok: false };
}

async function checkOvvConf() {
  const cfg = process.env.OPENVIKING_CONFIG_FILE || join(OPENVIKING_DIR, "ov.conf");
  try {
    await access(cfg);
    return { ok: true, path: cfg };
  } catch {
    return { ok: false, path: cfg };
  }
}

// ─── Config helpers ───

const DEFAULT_SERVER_PORT = 1933;
const DEFAULT_AGFS_PORT = 1833;
const DEFAULT_VLM_MODEL = "doubao-seed-1-8-251228";
const DEFAULT_EMBEDDING_MODEL = "doubao-embedding-vision-250615";

const DEFAULT_WORKSPACE = join(HOME, ".openviking", "data");

function buildOvvConfJson(opts = {}) {
  const {
    apiKey = "",
    serverPort = DEFAULT_SERVER_PORT,
    agfsPort = DEFAULT_AGFS_PORT,
    vlmModel = DEFAULT_VLM_MODEL,
    embeddingModel = DEFAULT_EMBEDDING_MODEL,
    workspace = DEFAULT_WORKSPACE,
  } = opts;
  return JSON.stringify({
    server: {
      host: "127.0.0.1",
      port: serverPort,
      root_api_key: null,
      cors_origins: ["*"],
    },
    storage: {
      workspace,
      vectordb: { name: "context", backend: "local", project: "default" },
      agfs: { port: agfsPort, log_level: "warn", backend: "local", timeout: 10, retry_times: 3 },
    },
    embedding: {
      dense: {
        backend: "volcengine",
        api_key: apiKey || null,
        model: embeddingModel,
        api_base: "https://ark.cn-beijing.volces.com/api/v3",
        dimension: 1024,
        input: "multimodal",
      },
    },
    vlm: {
      backend: "volcengine",
      api_key: apiKey || null,
      model: vlmModel,
      api_base: "https://ark.cn-beijing.volces.com/api/v3",
      temperature: 0.1,
      max_retries: 3,
    },
  }, null, 2);
}

function parsePort(val, defaultVal) {
  const n = parseInt(val, 10);
  return Number.isFinite(n) && n >= 1 && n <= 65535 ? n : defaultVal;
}

async function ensureOvvConf(cfgPath, opts = {}) {
  await mkdir(dirname(cfgPath), { recursive: true });
  await writeFile(cfgPath, buildOvvConfJson(opts));
  log(`Created config: ${cfgPath}`, "ok");
  if (!opts.apiKey) {
    log("API Key not set; memory features may be unavailable. Edit ov.conf to add later.", "warn");
  }
}

async function getApiKeyFromOvvConf(cfgPath) {
  let raw;
  try {
    raw = await readFile(cfgPath, "utf-8");
    const cfg = JSON.parse(raw);
    return cfg?.embedding?.dense?.api_key || "";
  } catch {
    const m = raw?.match(/api_key\s*:\s*["']?([^"'\s#]+)["']?/);
    return m ? m[1].trim() : "";
  }
}

async function getOvvConfPorts(cfgPath) {
  try {
    const raw = await readFile(cfgPath, "utf-8");
    const cfg = JSON.parse(raw);
    return {
      serverPort: cfg?.server?.port ?? DEFAULT_SERVER_PORT,
      agfsPort: cfg?.storage?.agfs?.port ?? DEFAULT_AGFS_PORT,
    };
  } catch {
    return { serverPort: DEFAULT_SERVER_PORT, agfsPort: DEFAULT_AGFS_PORT };
  }
}

async function isOvvConfInvalid(cfgPath) {
  try {
    JSON.parse(await readFile(cfgPath, "utf-8"));
    return false;
  } catch {
    return true;
  }
}

async function updateOvvConf(cfgPath, opts = {}) {
  let cfg;
  try {
    cfg = JSON.parse(await readFile(cfgPath, "utf-8"));
  } catch {
    log("ov.conf is not valid JSON, will create new config", "warn");
    await ensureOvvConf(cfgPath, opts);
    return;
  }
  if (opts.apiKey !== undefined) {
    if (!cfg.embedding) cfg.embedding = {};
    if (!cfg.embedding.dense) cfg.embedding.dense = {};
    cfg.embedding.dense.api_key = opts.apiKey || null;
    if (!cfg.vlm) cfg.vlm = {};
    cfg.vlm.api_key = opts.apiKey || null;
  }
  if (opts.vlmModel !== undefined) {
    if (!cfg.vlm) cfg.vlm = {};
    cfg.vlm.model = opts.vlmModel;
    if (!cfg.vlm.api_base) cfg.vlm.api_base = "https://ark.cn-beijing.volces.com/api/v3";
    if (!cfg.vlm.backend) cfg.vlm.backend = "volcengine";
  }
  if (opts.embeddingModel !== undefined) {
    if (!cfg.embedding) cfg.embedding = {};
    if (!cfg.embedding.dense) cfg.embedding.dense = {};
    cfg.embedding.dense.model = opts.embeddingModel;
  }
  if (opts.serverPort !== undefined && cfg.server) cfg.server.port = opts.serverPort;
  if (opts.agfsPort !== undefined && cfg.storage?.agfs) cfg.storage.agfs.port = opts.agfsPort;
  await writeFile(cfgPath, JSON.stringify(cfg, null, 2));
}

// ─── Interactive config collection ───

async function collectOvvConfInteractive(nonInteractive) {
  const opts = {
    apiKey: process.env.OPENVIKING_ARK_API_KEY || "",
    serverPort: DEFAULT_SERVER_PORT,
    agfsPort: DEFAULT_AGFS_PORT,
    vlmModel: DEFAULT_VLM_MODEL,
    embeddingModel: DEFAULT_EMBEDDING_MODEL,
    workspace: DEFAULT_WORKSPACE,
  };
  if (nonInteractive) return opts;

  console.log("\n╔══════════════════════════════════════════════════════════╗");
  console.log("║           OpenViking Configuration (ov.conf)            ║");
  console.log("╚══════════════════════════════════════════════════════════╝");

  console.log("\n--- Data Storage ---");
  console.log("Workspace is where OpenViking stores all data (vector database, files, etc.).");
  opts.workspace = await question(`Workspace path`, DEFAULT_WORKSPACE);

  console.log("\nOpenViking requires a Volcengine Ark API Key for:");
  console.log("  - Embedding model: vectorizes text for semantic search");
  console.log("  - VLM model: analyzes conversations to extract memories");
  console.log("\nGet your API Key at: https://console.volcengine.com/ark\n");

  opts.apiKey = (await questionApiKey("Volcengine Ark API Key (leave blank to skip, configure later): ")) || opts.apiKey;

  console.log("\n--- Model Configuration ---");
  console.log("VLM model is used to extract and analyze memories from conversations.");
  opts.vlmModel = await question(`VLM model name`, DEFAULT_VLM_MODEL);

  console.log("\nEmbedding model is used to vectorize text for semantic search.");
  opts.embeddingModel = await question(`Embedding model name`, DEFAULT_EMBEDDING_MODEL);

  console.log("\n--- Server Ports ---");
  const serverPortStr = await question(`OpenViking HTTP port`, String(DEFAULT_SERVER_PORT));
  opts.serverPort = parsePort(serverPortStr, DEFAULT_SERVER_PORT);
  const agfsPortStr = await question(`AGFS port`, String(DEFAULT_AGFS_PORT));
  opts.agfsPort = parsePort(agfsPortStr, DEFAULT_AGFS_PORT);

  return opts;
}

// ─── Installation helpers ───

async function installOpenviking(repoRoot) {
  const py = process.env.OPENVIKING_PYTHON || DEFAULT_PYTHON;
  log(`Installing openviking (using ${py})...`);
  if (repoRoot && existsSync(join(repoRoot, "pyproject.toml"))) {
    await run(py, ["-m", "pip", "install", "-e", repoRoot]);
    return;
  }
  await run(py, ["-m", "pip", "install", "openviking"]);
}

async function fetchPluginFromGitHub(dest) {
  log("Downloading memory-openviking plugin from GitHub...");
  const files = [
    "examples/openclaw-memory-plugin/index.ts",
    "examples/openclaw-memory-plugin/config.ts",
    "examples/openclaw-memory-plugin/openclaw.plugin.json",
    "examples/openclaw-memory-plugin/package.json",
    "examples/openclaw-memory-plugin/package-lock.json",
    "examples/openclaw-memory-plugin/.gitignore",
  ];
  await mkdir(dest, { recursive: true });
  for (let i = 0; i < files.length; i++) {
    const rel = files[i];
    const name = rel.split("/").pop();
    process.stdout.write(`  Downloading ${i + 1}/${files.length}: ${name} ... `);
    const url = `${GITHUB_RAW}/${rel}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Download failed: ${url}`);
    const buf = await res.arrayBuffer();
    await writeFile(join(dest, name), Buffer.from(buf));
    process.stdout.write("\u2713\n");
  }
  log(`Plugin downloaded to ${dest}`, "ok");
  process.stdout.write("  Installing plugin deps (npm install)... ");
  await run("npm", ["install", "--no-audit", "--no-fund"], { cwd: dest, silent: true });
  process.stdout.write("\u2713\n");
  log("Plugin deps installed", "ok");
}

async function fixStalePluginPaths(pluginPath) {
  const cfgPath = join(OPENCLAW_DIR, "openclaw.json");
  if (!existsSync(cfgPath)) return;
  try {
    const cfg = JSON.parse(await readFile(cfgPath, "utf8"));
    let changed = false;
    const paths = cfg?.plugins?.load?.paths;
    if (Array.isArray(paths)) {
      const cleaned = paths.filter((p) => existsSync(p));
      if (!cleaned.includes(pluginPath)) cleaned.push(pluginPath);
      if (JSON.stringify(cleaned) !== JSON.stringify(paths)) {
        cfg.plugins.load.paths = cleaned;
        changed = true;
      }
    }
    const installs = cfg?.plugins?.installs;
    if (installs) {
      for (const [k, v] of Object.entries(installs)) {
        if (v?.installPath && !existsSync(v.installPath)) {
          delete installs[k];
          changed = true;
        }
      }
    }
    if (changed) {
      await writeFile(cfgPath, JSON.stringify(cfg, null, 2) + "\n");
      log("Cleaned stale plugin paths from openclaw.json", "ok");
    }
  } catch {}
}

async function configureOpenclawViaJson(pluginPath, serverPort) {
  const cfgPath = join(OPENCLAW_DIR, "openclaw.json");
  let cfg = {};
  try { cfg = JSON.parse(await readFile(cfgPath, "utf8")); } catch { /* start fresh */ }
  if (!cfg.plugins) cfg.plugins = {};
  cfg.plugins.enabled = true;
  cfg.plugins.allow = ["memory-openviking"];
  if (!cfg.plugins.slots) cfg.plugins.slots = {};
  cfg.plugins.slots.memory = "memory-openviking";
  if (!cfg.plugins.load) cfg.plugins.load = {};
  const paths = Array.isArray(cfg.plugins.load.paths) ? cfg.plugins.load.paths : [];
  if (!paths.includes(pluginPath)) paths.push(pluginPath);
  cfg.plugins.load.paths = paths;
  if (!cfg.plugins.entries) cfg.plugins.entries = {};
  cfg.plugins.entries["memory-openviking"] = {
    config: {
      mode: "local",
      configPath: "~/.openviking/ov.conf",
      port: serverPort,
      targetUri: "viking://user/memories",
      autoRecall: true,
      autoCapture: true,
    },
  };
  if (!cfg.gateway) cfg.gateway = {};
  cfg.gateway.mode = "local";
  await mkdir(OPENCLAW_DIR, { recursive: true });
  await writeFile(cfgPath, JSON.stringify(cfg, null, 2) + "\n");
}

async function configureOpenclawViaCli(pluginPath, serverPort, mode) {
  const runNoShell = (cmd, args, opts = {}) => run(cmd, args, { ...opts, shell: false });
  if (mode === "link") {
    if (existsSync(PLUGIN_DEST)) {
      log(`Removing old plugin dir ${PLUGIN_DEST}...`, "info");
      await rm(PLUGIN_DEST, { recursive: true, force: true });
    }
    await run("openclaw", ["plugins", "install", "-l", pluginPath]);
  } else {
    await runNoShell("openclaw", ["config", "set", "plugins.load.paths", JSON.stringify([pluginPath])], { silent: true }).catch(() => {});
  }
  await runNoShell("openclaw", ["config", "set", "plugins.enabled", "true"]);
  await runNoShell("openclaw", ["config", "set", "plugins.allow", JSON.stringify(["memory-openviking"]), "--json"]);
  await runNoShell("openclaw", ["config", "set", "gateway.mode", "local"]);
  await runNoShell("openclaw", ["config", "set", "plugins.slots.memory", "memory-openviking"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.mode", "local"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.configPath", "~/.openviking/ov.conf"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.port", String(serverPort)]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.targetUri", "viking://user/memories"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.autoRecall", "true", "--json"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.autoCapture", "true", "--json"]);
}

async function configureOpenclaw(pluginPath, serverPort = DEFAULT_SERVER_PORT, mode = "link") {
  await fixStalePluginPaths(pluginPath);
  if (IS_WIN) {
    await configureOpenclawViaJson(pluginPath, serverPort);
  } else {
    await configureOpenclawViaCli(pluginPath, serverPort, mode);
  }
  log("OpenClaw plugin config done", "ok");
}

async function resolveCommand(cmd) {
  if (IS_WIN) {
    const { code, out } = await runCapture("where", [cmd], { shell: true });
    return code === 0 ? out.split(/\r?\n/)[0].trim() : "";
  }
  const { out } = await runCapture("sh", ["-c", `command -v ${cmd} 2>/dev/null || which ${cmd}`]);
  return out || "";
}

async function writeOpenvikingEnv() {
  const pyCmd = process.env.OPENVIKING_PYTHON || DEFAULT_PYTHON;
  const pyPath = await resolveCommand(pyCmd);
  const goOut = await resolveCommand("go");
  const goPath = goOut ? dirname(goOut) : "";
  await mkdir(OPENCLAW_DIR, { recursive: true });

  if (IS_WIN) {
    const lines = [];
    if (pyPath) lines.push(`set OPENVIKING_PYTHON=${pyPath}`);
    if (goPath) lines.push(`set OPENVIKING_GO_PATH=${goPath}`);
    if (process.env.GOPATH) lines.push(`set OPENVIKING_GOPATH=${process.env.GOPATH}`);
    if (process.env.GOPROXY) lines.push(`set OPENVIKING_GOPROXY=${process.env.GOPROXY}`);
    await writeFile(join(OPENCLAW_DIR, "openviking.env.bat"), lines.join("\r\n") + "\r\n");
    log(`Written ~/.openclaw/openviking.env.bat`, "ok");
  } else {
    const lines = [];
    if (pyPath) lines.push(`export OPENVIKING_PYTHON='${pyPath}'`);
    if (goPath) lines.push(`export OPENVIKING_GO_PATH='${goPath}'`);
    if (process.env.GOPATH) lines.push(`export OPENVIKING_GOPATH='${process.env.GOPATH}'`);
    if (process.env.GOPROXY) lines.push(`export OPENVIKING_GOPROXY='${process.env.GOPROXY}'`);
    await writeFile(join(OPENCLAW_DIR, "openviking.env"), lines.join("\n") + "\n");
    log(`Written ~/.openclaw/openviking.env`, "ok");
  }
}

// ─── Main flow ───

async function main() {
  const args = process.argv.slice(2);
  const help = args.includes("--help") || args.includes("-h");
  const nonInteractive = args.includes("--yes") || args.includes("-y");

  if (help) {
    console.log(`
OpenClaw + OpenViking setup helper

Usage: npx openclaw-openviking-setup-helper [options]

Options:
  -y, --yes     Non-interactive, use defaults
  -h, --help    Show help

Steps:
  1. Check OpenClaw
  2. Check build environment (Python, Go, cmake, g++)
  3. Install openviking module if needed
  4. Configure ov.conf (API Key, VLM, Embedding, ports)
  5. Deploy memory-openviking plugin
  6. Write ~/.openclaw/openviking.env

Env vars:
  OPENVIKING_PYTHON       Python path
  OPENVIKING_CONFIG_FILE  ov.conf path
  OPENVIKING_REPO         Local OpenViking repo path (use local plugin if set)
  OPENVIKING_ARK_API_KEY  Volcengine Ark API Key (used in -y mode, skip prompt)
  OPENVIKING_GO_PATH      Go bin dir (when Go not in PATH, e.g. ~/local/go/bin)
`);
    process.exit(0);
  }

  console.log("\n\ud83e\udd9e OpenClaw + OpenViking setup helper\n");

  const distro = await detectDistro();

  // ════════════════════════════════════════════
  // Phase 1: Check build tools & runtime environment
  //   cmake/g++ must be present before OpenClaw install (node-llama-cpp needs them)
  // ════════════════════════════════════════════
  console.log("── Step 1/5: Checking build environment ──\n");

  const missing = [];

  // cmake check (needed by OpenClaw's node-llama-cpp AND OpenViking C++ extension)
  const cmakeResult = await checkCmake();
  if (cmakeResult.ok) {
    log("cmake: installed", "ok");
  } else {
    log("cmake: not found", "err");
    missing.push({ name: "cmake", detail: "Required by OpenClaw (llama.cpp) and OpenViking (C++ extension)" });
  }

  // g++ check (needed by OpenClaw's node-llama-cpp AND OpenViking C++ extension)
  const gppResult = await checkGpp();
  if (gppResult.ok) {
    log("g++: installed", "ok");
  } else {
    log("g++: not found", "err");
    missing.push({ name: "g++ (gcc-c++)", detail: "Required by OpenClaw (llama.cpp) and OpenViking (C++ extension)" });
  }

  // Python check
  const pyResult = await checkPython();
  if (pyResult.ok) {
    log(`Python: ${pyResult.msg}`, "ok");
  } else {
    log(`Python: ${pyResult.msg}`, "err");
    missing.push({ name: "Python >= 3.10", detail: pyResult.version ? `Current: ${pyResult.version}` : "Not found" });
  }

  // Go check (required on Linux for source install)
  const goResult = await checkGo();
  if (goResult.ok) {
    log(`Go: ${goResult.msg}`, "ok");
  } else if (IS_LINUX) {
    log(`Go: ${goResult.msg}`, "err");
    missing.push({ name: "Go >= 1.25", detail: goResult.msg });
  } else {
    log(`Go: not found (not required on ${process.platform})`, "warn");
  }

  if (missing.length > 0) {
    console.log("\n\u2717 Missing dependencies:\n");
    for (const m of missing) {
      console.log(`  - ${m.name}: ${m.detail}`);
    }

    console.log("\n  Please install the missing dependencies:\n");

    if (distro === "rhel") {
      const needBuild = missing.some((m) => m.name === "cmake" || m.name === "g++ (gcc-c++)");
      const needPython = missing.some((m) => m.name.startsWith("Python"));
      const needGo = missing.some((m) => m.name.startsWith("Go"));

      if (needBuild) console.log("    sudo dnf install -y gcc gcc-c++ cmake make");
      if (needPython) {
        console.log("    # Install Python 3.11 (try package manager first):");
        console.log("    sudo dnf install -y python3.11 python3.11-devel python3.11-pip");
        console.log("    # If unavailable, build from source:");
        console.log("    #   See INSTALL-ZH.md 'Linux Environment Setup' section");
      }
      if (needGo) {
        console.log("    # Install Go >= 1.25:");
        console.log("    wget https://go.dev/dl/go1.25.6.linux-amd64.tar.gz");
        console.log("    sudo rm -rf /usr/local/go");
        console.log("    sudo tar -C /usr/local -xzf go1.25.6.linux-amd64.tar.gz");
        console.log("    echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc");
        console.log("    source ~/.bashrc");
        console.log("    # Configure Go module proxy (recommended if downloads are slow):");
        console.log("    go env -w GOPROXY=https://goproxy.cn,direct");
      }
    } else if (distro === "debian") {
      const needBuild = missing.some((m) => m.name === "cmake" || m.name === "g++ (gcc-c++)");
      const needPython = missing.some((m) => m.name.startsWith("Python"));
      const needGo = missing.some((m) => m.name.startsWith("Go"));

      if (needBuild) console.log("    sudo apt update && sudo apt install -y build-essential cmake");
      if (needPython) {
        console.log("    # Install Python 3.11:");
        console.log("    sudo add-apt-repository ppa:deadsnakes/ppa");
        console.log("    sudo apt install -y python3.11 python3.11-dev python3.11-venv");
      }
      if (needGo) {
        console.log("    # Install Go >= 1.25:");
        console.log("    wget https://go.dev/dl/go1.25.6.linux-amd64.tar.gz");
        console.log("    sudo rm -rf /usr/local/go");
        console.log("    sudo tar -C /usr/local -xzf go1.25.6.linux-amd64.tar.gz");
        console.log("    echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc");
        console.log("    source ~/.bashrc");
        console.log("    # Configure Go module proxy (recommended if downloads are slow):");
        console.log("    go env -w GOPROXY=https://goproxy.cn,direct");
      }
    } else {
      console.log("    Please install: cmake, g++, Python >= 3.10, Go >= 1.25");
      console.log("    See INSTALL-ZH.md for detailed instructions.");
    }

    console.log("\n  After installing, re-run this script:");
    console.log("    npx ./examples/openclaw-memory-plugin/setup-helper\n");
    process.exit(1);
  }

  // ════════════════════════════════════════════
  // Phase 2: Check OpenClaw
  // ════════════════════════════════════════════
  console.log("\n── Step 2/5: Checking OpenClaw ──\n");

  const hasOpenclaw = await checkOpenclaw();
  if (!hasOpenclaw.ok) {
    log("OpenClaw is not installed.", "err");
    console.log("\n  Please install OpenClaw:\n");
    console.log("    npm install -g openclaw\n");
    console.log("  If downloads are slow, use npmmirror registry:");
    console.log("    npm install -g openclaw --registry=https://registry.npmmirror.com\n");
    console.log("  After installation, run onboarding to configure your LLM:");
    console.log("    openclaw onboard\n");
    console.log("  Then re-run this script:");
    console.log("    npx ./examples/openclaw-memory-plugin/setup-helper\n");
    process.exit(1);
  }
  log("OpenClaw: installed", "ok");

  // ════════════════════════════════════════════
  // Phase 3: Check & install openviking module
  // ════════════════════════════════════════════
  console.log("\n── Step 3/5: Checking openviking module ──\n");

  const ovMod = await checkOpenvikingModule();
  if (ovMod.ok) {
    log("openviking module: installed", "ok");
  } else {
    log("openviking module: not found", "warn");
    const inferredRepoRoot = join(__dirname, "..", "..", "..");
    const hasLocalRepo = existsSync(join(inferredRepoRoot, "pyproject.toml"));
    const repo = process.env.OPENVIKING_REPO || (hasLocalRepo ? inferredRepoRoot : "");

    if (nonInteractive) {
      await installOpenviking(repo);
    } else {
      const choice = await question(
        repo
          ? "Install openviking from local repo? (y=local repo / n=skip)"
          : "Install openviking from PyPI? (y/n)",
        "y"
      );
      if (choice.toLowerCase() === "y") {
        await installOpenviking(repo);
      } else {
        log("Please install openviking manually and re-run this script.", "err");
        if (repo) console.log(`    cd ${repo} && python3.11 -m pip install -e .`);
        else console.log("    python3.11 -m pip install openviking");
        process.exit(1);
      }
    }

    const recheck = await checkOpenvikingModule();
    if (!recheck.ok) {
      log("openviking module installation failed. Check errors above.", "err");
      process.exit(1);
    }
    log("openviking module: installed", "ok");
  }

  // ════════════════════════════════════════════
  // Phase 4: Configure ov.conf (interactive)
  // ════════════════════════════════════════════
  console.log("\n── Step 4/5: Configuring OpenViking ──\n");

  const ovConf = await checkOvvConf();
  const ovConfPath = ovConf.path;
  let ovOpts = {
    apiKey: process.env.OPENVIKING_ARK_API_KEY || "",
    serverPort: DEFAULT_SERVER_PORT,
    agfsPort: DEFAULT_AGFS_PORT,
    vlmModel: DEFAULT_VLM_MODEL,
    embeddingModel: DEFAULT_EMBEDDING_MODEL,
  };

  if (!ovConf.ok) {
    log(`ov.conf not found: ${ovConfPath}`, "info");
    const create = nonInteractive || (await question("Create ov.conf now? (y/n)", "y")).toLowerCase() === "y";
    if (create) {
      ovOpts = await collectOvvConfInteractive(nonInteractive);
      await ensureOvvConf(ovConfPath, ovOpts);
    } else {
      log("Please create ~/.openviking/ov.conf manually", "err");
      process.exit(1);
    }
  } else {
    log(`ov.conf found: ${ovConfPath}`, "ok");
    const invalid = await isOvvConfInvalid(ovConfPath);
    const existingKey = await getApiKeyFromOvvConf(ovConfPath);
    const existingPorts = await getOvvConfPorts(ovConfPath);

    if (invalid) {
      log("ov.conf format is invalid, will recreate", "warn");
      ovOpts = await collectOvvConfInteractive(nonInteractive);
      await ensureOvvConf(ovConfPath, ovOpts);
    } else if (!existingKey && !nonInteractive) {
      log("API Key is not configured in ov.conf", "warn");
      console.log("\nOpenViking needs a Volcengine Ark API Key for memory features.");
      console.log("Get your API Key at: https://console.volcengine.com/ark\n");
      const apiKey = (await questionApiKey("Volcengine Ark API Key (leave blank to skip): ")) || process.env.OPENVIKING_ARK_API_KEY || "";
      if (apiKey) {
        await updateOvvConf(ovConfPath, { apiKey });
        log("Written API Key to ov.conf", "ok");
      } else {
        log("API Key not set; memory features may be unavailable. Edit ov.conf to add later.", "warn");
      }
      ovOpts = { ...existingPorts, apiKey };
    } else if (!existingKey && process.env.OPENVIKING_ARK_API_KEY) {
      await updateOvvConf(ovConfPath, { apiKey: process.env.OPENVIKING_ARK_API_KEY });
      log("Written API Key from env to ov.conf", "ok");
      ovOpts = { ...existingPorts, apiKey: process.env.OPENVIKING_ARK_API_KEY };
    } else {
      ovOpts = { ...existingPorts, apiKey: existingKey };
    }
  }

  // ════════════════════════════════════════════
  // Phase 5: Deploy plugin & finalize
  // ════════════════════════════════════════════
  console.log("\n── Step 5/5: Deploying plugin ──\n");

  const inferredRepoRoot = join(__dirname, "..", "..", "..");
  const repoRoot = process.env.OPENVIKING_REPO ||
    (existsSync(join(inferredRepoRoot, "examples", "openclaw-memory-plugin", "index.ts")) ? inferredRepoRoot : "");
  let pluginPath;
  if (repoRoot && existsSync(join(repoRoot, "examples", "openclaw-memory-plugin", "index.ts"))) {
    pluginPath = join(repoRoot, "examples", "openclaw-memory-plugin");
    log(`Using local plugin: ${pluginPath}`, "ok");
    if (!existsSync(join(pluginPath, "node_modules"))) {
      await run("npm", ["install", "--no-audit", "--no-fund"], { cwd: pluginPath, silent: true });
    }
  } else {
    await fetchPluginFromGitHub(PLUGIN_DEST);
    pluginPath = PLUGIN_DEST;
  }

  await configureOpenclaw(pluginPath, ovOpts?.serverPort);
  await writeOpenvikingEnv();

  // Done
  console.log("\n╔══════════════════════════════════════════════════════════╗");
  console.log("║                   \u2705 Setup complete!                     ║");
  console.log("╚══════════════════════════════════════════════════════════╝");
  console.log("\nTo start OpenClaw with memory:");
  if (IS_WIN) {
    console.log('  call "%USERPROFILE%\\.openclaw\\openviking.env.bat" && openclaw gateway');
  } else {
    console.log("  source ~/.openclaw/openviking.env && openclaw gateway");
  }
  console.log("\nTo verify:");
  console.log("  openclaw status");
  console.log("");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
