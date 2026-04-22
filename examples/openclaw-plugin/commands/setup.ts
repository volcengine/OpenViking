import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as readline from "node:readline";
import { launchProcess, sysEnv, getEnv, parseWindowsEnvBatPythonPath, parsePosixEnvPythonPath } from "../runtime-utils.js";

const IS_WIN = os.platform() === "win32";
const HOME = os.homedir();
const OPENCLAW_DIR = getEnv("OPENCLAW_STATE_DIR") || path.join(HOME, ".openclaw");
const DEFAULT_CONFIG_PATH = path.join(HOME, ".openviking", "ov.conf");
const DEFAULT_PORT = 1933;
const DEFAULT_REMOTE_URL = "http://127.0.0.1:1933";

type CommandProgram = {
  command: (name: string) => CommandBuilder;
};

type CommandBuilder = {
  description: (desc: string) => CommandBuilder;
  option: (flags: string, desc: string) => CommandBuilder;
  command: (name: string) => CommandBuilder;
  action: (fn: (...args: unknown[]) => void | Promise<void>) => CommandBuilder;
};

function tr(langZh: boolean, en: string, zh: string): string {
  return langZh ? zh : en;
}

function maskKey(key: string): string {
  if (key.length <= 8) return "****";
  return `${key.slice(0, 4)}...${key.slice(-4)}`;
}

function ask(rl: readline.Interface, prompt: string, defaultValue = ""): Promise<string> {
  const suffix = defaultValue ? ` [${defaultValue}]` : "";
  return new Promise((resolve) => {
    rl.question(`${prompt}${suffix}: `, (answer) => {
      resolve((answer ?? "").trim() || defaultValue);
    });
  });
}

function capture(
  cmd: string,
  args: string[],
  opts?: { env?: NodeJS.ProcessEnv; shell?: boolean },
): Promise<{ code: number; out: string; err: string }> {
  return new Promise((resolve) => {
    const child = launchProcess(cmd, args, {
      stdio: ["ignore", "pipe", "pipe"],
      env: opts?.env ?? sysEnv(),
      shell: opts?.shell ?? false,
    });
    let out = "";
    let errOut = "";
    child.stdout?.on("data", (chunk: Buffer) => { out += String(chunk); });
    child.stderr?.on("data", (chunk: Buffer) => { errOut += String(chunk); });
    child.on("error", (error: Error) => { resolve({ code: -1, out: "", err: String(error) }); });
    child.on("close", (code: number | null) => { resolve({ code: code ?? -1, out: out.trim(), err: errOut.trim() }); });
  });
}

async function resolveAbsoluteCommand(cmd: string): Promise<string> {
  if (cmd.startsWith("/") || (IS_WIN && /^[A-Za-z]:[/\\]/.test(cmd))) return cmd;
  if (IS_WIN) {
    const r = await capture("where", [cmd], { shell: true });
    return r.out.split(/\r?\n/)[0]?.trim() || cmd;
  }
  const r = await capture("which", [cmd]);
  return r.out.trim() || cmd;
}

async function resolvePythonCmd(): Promise<string> {
  const envPython = getEnv("OPENVIKING_PYTHON");
  if (envPython) return resolveAbsoluteCommand(envPython);

  const defaultDir = path.join(HOME, ".openclaw");
  const searchDirs = OPENCLAW_DIR !== defaultDir
    ? [OPENCLAW_DIR, defaultDir]
    : [defaultDir];
  for (const dir of searchDirs) {
    const envFile = IS_WIN
      ? path.join(dir, "openviking.env.bat")
      : path.join(dir, "openviking.env");
    if (fs.existsSync(envFile)) {
      try {
        const content = fs.readFileSync(envFile, "utf-8");
        const parsed = IS_WIN
          ? parseWindowsEnvBatPythonPath(content)
          : parsePosixEnvPythonPath(content);
        if (parsed) return parsed;
      } catch { /* ignore */ }
    }
  }

  const venvPy = IS_WIN
    ? path.join(HOME, ".openviking", "venv", "Scripts", "python.exe")
    : path.join(HOME, ".openviking", "venv", "bin", "python");
  if (fs.existsSync(venvPy)) return venvPy;

  const raw = IS_WIN ? "python" : "python3";
  return resolveAbsoluteCommand(raw);
}

async function checkOpenVikingInstalled(): Promise<{ ok: boolean; version: string; pythonPath: string }> {
  const pythonCmd = await resolvePythonCmd();
  const result = await capture(pythonCmd, ["-c", "import openviking; print(openviking.__version__)"]);
  if (result.code === 0 && result.out) {
    return { ok: true, version: result.out, pythonPath: pythonCmd };
  }

  const venvPy = IS_WIN
    ? path.join(HOME, ".openviking", "venv", "Scripts", "python.exe")
    : path.join(HOME, ".openviking", "venv", "bin", "python");
  const candidates = IS_WIN
    ? ["python", "py"]
    : [venvPy, "python3.13", "python3.12", "python3.11", "python3.10", "python3"];
  const tried = new Set<string>([pythonCmd]);
  for (const candidate of candidates) {
    const resolved = candidate.startsWith("/") && fs.existsSync(candidate)
      ? candidate
      : await resolveAbsoluteCommand(candidate);
    if (!resolved || tried.has(resolved)) continue;
    tried.add(resolved);
    const check = await capture(resolved, ["-c", "import openviking; print(openviking.__version__)"]);
    if (check.code === 0 && check.out) {
      return { ok: true, version: check.out, pythonPath: resolved };
    }
  }

  return { ok: false, version: "", pythonPath: "" };
}

function writeOpenvikingEnv(pythonPath: string): void {
  if (!fs.existsSync(OPENCLAW_DIR)) fs.mkdirSync(OPENCLAW_DIR, { recursive: true });
  if (IS_WIN) {
    const batSafe = pythonPath.replace(/"/g, '""');
    const ps1Safe = pythonPath.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\$/g, '`$');
    fs.writeFileSync(path.join(OPENCLAW_DIR, "openviking.env.bat"), `@echo off\r\nset "OPENVIKING_PYTHON=${batSafe}"\r\n`, "utf-8");
    fs.writeFileSync(path.join(OPENCLAW_DIR, "openviking.env.ps1"), `$env:OPENVIKING_PYTHON = "${ps1Safe}"\n`, "utf-8");
  } else {
    const shSafe = pythonPath.replace(/'/g, "'\"'\"'");
    fs.writeFileSync(path.join(OPENCLAW_DIR, "openviking.env"), `export OPENVIKING_PYTHON='${shSafe}'\n`, "utf-8");
  }
}

type LocalConfigValidationResult = {
  ok: boolean;
  error: string;
  usedPythonValidation: boolean;
};

function getErrorMessage(error: unknown): string {
  return String(error instanceof Error ? error.message : error);
}

function summarizeValidationOutput(output: string): string {
  const lines = output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return output.trim();
  const preferred = lines.find((line) => /^((ValueError|FileNotFoundError|SystemExit|RuntimeError):|Invalid )/.test(line));
  return preferred || lines[lines.length - 1];
}

async function validateLocalConfigPath(configPath: string, pythonCmd?: string): Promise<LocalConfigValidationResult> {
  if (!fs.existsSync(configPath)) {
    return {
      ok: false,
      error: `Config file not found: ${configPath}`,
      usedPythonValidation: false,
    };
  }

  let raw = "";
  try {
    raw = fs.readFileSync(configPath, "utf-8");
  } catch (error) {
    return {
      ok: false,
      error: `Failed to read config file: ${getErrorMessage(error)}`,
      usedPythonValidation: false,
    };
  }

  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {
        ok: false,
        error: "Invalid ov.conf: top-level JSON value must be an object",
        usedPythonValidation: false,
      };
    }
  } catch (error) {
    return {
      ok: false,
      error: `Invalid ov.conf JSON: ${getErrorMessage(error)}`,
      usedPythonValidation: false,
    };
  }

  if (!pythonCmd) {
    return { ok: true, error: "", usedPythonValidation: false };
  }

  const validationCode = [
    "import sys",
    "from openviking.server.config import load_server_config, validate_server_config",
    "validate_server_config(load_server_config(sys.argv[1]))",
    "print('ok')",
  ].join(";");
  const result = await capture(pythonCmd, ["-c", validationCode, configPath]);
  if (result.code === 0) {
    return { ok: true, error: "", usedPythonValidation: true };
  }

  const detail = summarizeValidationOutput(result.err || result.out || `exit code ${result.code}`);
  return {
    ok: false,
    error: `ov.conf failed OpenViking validation: ${detail}`,
    usedPythonValidation: true,
  };
}

function printLocalConfigFixHint(zh: boolean, configPath: string, validationError: string): void {
  console.log("");
  console.log(`  ⚠ ${tr(zh,
    validationError,
    validationError,
  )}`);
  console.log(`    ${tr(zh,
    "OpenViking requires a valid ov.conf before local mode can be saved.",
    "OpenViking 本地模式需要有效的 ov.conf，配置向导不会保存无效配置。",
  )}`);
  console.log(`    ${tr(zh,
    "  Option 1: Run 'openviking init' to generate a default config",
    "  方式 1：运行 'openviking init' 生成默认配置",
  )}`);
  console.log(`    ${tr(zh,
    "  Option 2: Copy from examples/ov.conf.example",
    "  方式 2：从 examples/ov.conf.example 复制",
  )}`);
  console.log(`    ${tr(zh,
    `  Target path: ${configPath}`,
    `  目标路径: ${configPath}`,
  )}`);
  console.log("");
}

async function checkServiceHealth(baseUrl: string, apiKey?: string): Promise<{ ok: boolean; version: string; error: string }> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10_000);
  try {
    const headers: Record<string, string> = {};
    if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
    const response = await fetch(`${baseUrl.replace(/\/+$/, "")}/health`, {
      headers,
      signal: controller.signal,
    });
    if (response.ok) {
      try {
        const data = await response.json() as Record<string, unknown>;
        return { ok: true, version: String(data.version ?? ""), error: "" };
      } catch {
        return { ok: true, version: "", error: "" };
      }
    }
    return { ok: false, version: "", error: `HTTP ${response.status}` };
  } catch (err) {
    return { ok: false, version: "", error: String(err instanceof Error ? err.message : err) };
  } finally {
    clearTimeout(timeoutId);
  }
}

function readOpenClawConfig(configPath: string): Record<string, unknown> {
  if (!fs.existsSync(configPath)) return {};
  try {
    return JSON.parse(fs.readFileSync(configPath, "utf-8"));
  } catch {
    return {};
  }
}

function getExistingPluginConfig(config: Record<string, unknown>): Record<string, unknown> | null {
  const plugins = config.plugins as Record<string, unknown> | undefined;
  if (!plugins) return null;
  const entries = plugins.entries as Record<string, unknown> | undefined;
  if (!entries) return null;
  const entry = entries.openviking as Record<string, unknown> | undefined;
  if (!entry) return null;
  const cfg = entry.config as Record<string, unknown> | undefined;
  return cfg && cfg.mode ? cfg : null;
}

function writeConfig(
  configPath: string,
  pluginCfg: Record<string, unknown>,
): void {
  const configDir = path.dirname(configPath);
  if (!fs.existsSync(configDir)) fs.mkdirSync(configDir, { recursive: true });

  const config = readOpenClawConfig(configPath);

  if (!config.plugins) config.plugins = {};
  const plugins = config.plugins as Record<string, unknown>;
  if (!plugins.entries) plugins.entries = {};
  const entries = plugins.entries as Record<string, unknown>;

  const existingEntry = (entries.openviking as Record<string, unknown>) ?? {};
  entries.openviking = { ...existingEntry, config: pluginCfg };

  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n", "utf-8");
}

function detectLangZh(options: Record<string, unknown>): boolean {
  if (options.zh) return true;
  const lang = getEnv("LANG") || getEnv("LC_ALL") || "";
  return /^zh/i.test(lang);
}

type SetupLocalDeps = {
  checkOpenVikingInstalled: typeof checkOpenVikingInstalled;
  writeOpenvikingEnv: typeof writeOpenvikingEnv;
  checkServiceHealth: typeof checkServiceHealth;
  validateLocalConfigPath: typeof validateLocalConfigPath;
  writeConfig: typeof writeConfig;
};

const defaultSetupLocalDeps: SetupLocalDeps = {
  checkOpenVikingInstalled,
  writeOpenvikingEnv,
  checkServiceHealth,
  validateLocalConfigPath,
  writeConfig,
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function registerSetupCli(api: any): void {
  if (!api.registerCli) {
    api.logger.info("openviking: registerCli not available, setup command skipped");
    return;
  }

  api.registerCli(
    ({ program: rawProgram }) => {
      const program = rawProgram as CommandProgram;
      const ovCmd = program.command("openviking").description("OpenViking plugin commands");

      ovCmd
        .command("setup")
        .description("Interactive setup wizard for OpenViking plugin configuration")
        .option("--reconfigure", "Force re-entry of all configuration values")
        .option("--zh", "Chinese prompts")
        .action(async (options: { reconfigure?: boolean; zh?: boolean }) => {
          const zh = detectLangZh(options as Record<string, unknown>);
          const configDir = OPENCLAW_DIR;
          const configPath = path.join(configDir, "openclaw.json");

          console.log("");
          console.log(`🦣 ${tr(zh, "OpenViking Plugin Setup", "OpenViking 插件配置向导")}`);
          console.log("");

          const config = readOpenClawConfig(configPath);
          const existing = getExistingPluginConfig(config);

          const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
          const q = (prompt: string, def = "") => ask(rl, prompt, def);

          try {
            if (existing && !options.reconfigure) {
              console.log(tr(zh, "Existing configuration found:", "已找到现有配置："));
              if (existing.mode === "remote") {
                console.log(`  mode:    ${existing.mode}`);
                console.log(`  baseUrl: ${existing.baseUrl ?? DEFAULT_REMOTE_URL}`);
                if (existing.apiKey) console.log(`  apiKey:  ${maskKey(String(existing.apiKey))}`);
                if (existing.agentId) console.log(`  agentId: ${existing.agentId}`);
              } else {
                console.log(`  mode:       ${existing.mode ?? "local"}`);
                console.log(`  configPath: ${existing.configPath ?? DEFAULT_CONFIG_PATH}`);
                console.log(`  port:       ${existing.port ?? DEFAULT_PORT}`);
              }
              console.log("");
              console.log(tr(
                zh,
                "Press Enter to keep existing values, or use --reconfigure to change.",
                "按 Enter 保留现有配置，或使用 --reconfigure 重新配置。",
              ));
              console.log("");
              console.log(tr(zh, "✓ Using existing configuration", "✓ 使用现有配置"));
              console.log("");

              // Environment checks for existing local config
              let envOk = true;
              if (!existing.mode || existing.mode === "local") {
                envOk = await runLocalChecks(zh, existing, q);
              } else {
                await runRemoteCheck(zh, existing);
              }

              if (envOk) {
                console.log(tr(zh,
                  "✓ Plugin is ready. Run `openclaw gateway --force` to activate.",
                  "✓ 插件已就绪。运行 `openclaw gateway --force` 以激活。",
                ));
              }
              console.log("");
              return;
            }

            if (existing && options.reconfigure) {
              console.log(tr(zh, "Existing configuration found:", "已找到现有配置："));
              if (existing.mode === "remote") {
                console.log(`  mode:    ${existing.mode}`);
                console.log(`  baseUrl: ${existing.baseUrl ?? DEFAULT_REMOTE_URL}`);
                if (existing.apiKey) console.log(`  apiKey:  ${maskKey(String(existing.apiKey))}`);
              } else {
                console.log(`  mode:       ${existing.mode ?? "local"}`);
                console.log(`  configPath: ${existing.configPath ?? DEFAULT_CONFIG_PATH}`);
                console.log(`  port:       ${existing.port ?? DEFAULT_PORT}`);
              }
              console.log("");
              console.log(tr(zh, "Reconfiguring...", "重新配置中..."));
              console.log("");
            } else {
              console.log(tr(zh,
                "No existing configuration found. Starting setup wizard.",
                "未找到现有配置，开始配置向导。",
              ));
              console.log("");
            }

            const modeDefault = String(existing?.mode ?? "local");
            const modeInput = await q(
              tr(zh, "Plugin mode - local or remote", "插件模式 - local 或 remote"),
              modeDefault,
            );
            const mode = modeInput.toLowerCase() === "remote" ? "remote" : "local";

            if (mode === "local") {
              const setupOk = await setupLocal(zh, configPath, existing, q);
              if (!setupOk) {
                console.log(tr(
                  zh,
                  "Local setup aborted. Fix the ov.conf issue above, then rerun `openclaw openviking setup`.",
                  "本地模式配置已中止。请先修复上面的 ov.conf 问题，再重新运行 `openclaw openviking setup`。",
                ));
                console.log("");
              }
            } else {
              await setupRemote(zh, configPath, existing, q);
            }
          } finally {
            rl.close();
          }
        });
    },
    { commands: ["openviking"] },
  );
}

async function runLocalChecks(
  zh: boolean,
  existing: Record<string, unknown>,
  _q: (prompt: string, def?: string) => Promise<string>,
): Promise<boolean> {
  console.log(tr(zh, "Checking environment...", "正在检查环境..."));
  const ov = await checkOpenVikingInstalled();
  if (ov.ok) {
    console.log(`  OpenViking: ${ov.version} ✓`);
    console.log(`  Python:     ${ov.pythonPath}`);
    writeOpenvikingEnv(ov.pythonPath);
  } else {
    console.log(`  ℹ ${tr(zh,
      "OpenViking Python package not detected (service may still work if installed separately)",
      "未检测到 OpenViking Python 包（如已单独安装服务，可忽略此提示）",
    )}`);
  }
  console.log("");

  const configPath = String(existing.configPath ?? DEFAULT_CONFIG_PATH);
  const configValidation = await validateLocalConfigPath(configPath, ov.ok ? ov.pythonPath : undefined);
  if (!configValidation.ok) {
    printLocalConfigFixHint(zh, configPath, configValidation.error);
    return false;
  }

  const port = Number(existing.port) || DEFAULT_PORT;
  console.log(tr(zh, `Checking OpenViking service on port ${port}...`, `正在检查 OpenViking 服务 (端口 ${port})...`));
  const health = await checkServiceHealth(`http://127.0.0.1:${port}`);
  if (health.ok) {
    const ver = health.version ? ` (version: ${health.version})` : "";
    console.log(`  ✓ ${tr(zh, `OpenViking service is running${ver}`, `OpenViking 服务正在运行${ver}`)}`);
  } else {
    console.log(`  ℹ ${tr(zh,
      `OpenViking service is not running on port ${port} (will auto-start with openclaw gateway)`,
      `OpenViking 服务未在端口 ${port} 运行（将随 openclaw gateway 自动启动）`,
    )}`);
  }
  console.log("");
  return true;
}

async function runRemoteCheck(
  zh: boolean,
  existing: Record<string, unknown>,
): Promise<void> {
  const baseUrl = String(existing.baseUrl ?? DEFAULT_REMOTE_URL);
  const apiKey = existing.apiKey ? String(existing.apiKey) : undefined;
  console.log(tr(zh, `Testing connectivity to ${baseUrl}...`, `正在测试连接 ${baseUrl}...`));
  const health = await checkServiceHealth(baseUrl, apiKey);
  if (health.ok) {
    const ver = health.version ? ` (version: ${health.version})` : "";
    console.log(`  ✓ ${tr(zh, `Connected successfully${ver}`, `连接成功${ver}`)}`);
  } else {
    console.log(`  ✗ ${tr(zh, `Connection failed: ${health.error}`, `连接失败: ${health.error}`)}`);
  }
  console.log("");
}

async function setupLocal(
  zh: boolean,
  configPath: string,
  existing: Record<string, unknown> | null,
  q: (prompt: string, def?: string) => Promise<string>,
  deps: Partial<SetupLocalDeps> = {},
): Promise<boolean> {
  const resolvedDeps = { ...defaultSetupLocalDeps, ...deps };
  // Environment check (non-blocking)
  console.log("");
  console.log(tr(zh, "Checking environment...", "正在检查环境..."));
  const ov = await resolvedDeps.checkOpenVikingInstalled();
  if (ov.ok) {
    console.log(`  OpenViking: ${ov.version} ✓`);
    console.log(`  Python:     ${ov.pythonPath}`);
    resolvedDeps.writeOpenvikingEnv(ov.pythonPath);
  } else {
    console.log(`  ⚠ ${tr(zh,
      "OpenViking Python package not detected. Make sure it is installed:",
      "未检测到 OpenViking Python 包，请确保已安装：",
    )}`);
    console.log("    pip install openviking");
  }
  console.log("");

  // Configuration
  console.log(tr(zh, "── Local Mode Configuration ──", "── 本地模式配置 ──"));
  console.log("");

  const defaultConfigPath = existing?.configPath ? String(existing.configPath) : DEFAULT_CONFIG_PATH;
  const defaultPort = existing?.port ? String(existing.port) : String(DEFAULT_PORT);

  const cfgPath = await q(tr(zh, "Config path", "配置文件路径"), defaultConfigPath);
  const configValidation = await resolvedDeps.validateLocalConfigPath(cfgPath, ov.ok ? ov.pythonPath : undefined);
  if (!configValidation.ok) {
    printLocalConfigFixHint(zh, cfgPath, configValidation.error);
    return false;
  }

  const portStr = await q(tr(zh, "Port", "端口"), defaultPort);
  const port = Math.max(1, Math.min(65535, parseInt(portStr, 10) || DEFAULT_PORT));

  console.log("");

  // Service health check (non-blocking)
  console.log(tr(zh, `Checking OpenViking service on port ${port}...`, `正在检查 OpenViking 服务 (端口 ${port})...`));
  const health = await resolvedDeps.checkServiceHealth(`http://127.0.0.1:${port}`);
  if (health.ok) {
    const ver = health.version ? ` (version: ${health.version})` : "";
    console.log(`  ✓ ${tr(zh, `OpenViking service is running${ver}`, `OpenViking 服务正在运行${ver}`)}`);
  } else {
    console.log(`  ℹ ${tr(zh,
      `OpenViking service is not running on port ${port} (will auto-start with openclaw gateway)`,
      `OpenViking 服务未在端口 ${port} 运行（将随 openclaw gateway 自动启动）`,
    )}`);
  }
  console.log("");

  // Write config
  const pluginCfg: Record<string, unknown> = {
    ...(existing ?? {}),
    mode: "local",
    configPath: cfgPath,
    port,
  };
  delete pluginCfg.baseUrl;

  resolvedDeps.writeConfig(configPath, pluginCfg);

  console.log("");
  console.log(`  ${tr(zh, "mode:", "模式:")}       local`);
  console.log(`  ${tr(zh, "configPath:", "配置文件:")}  ${cfgPath}`);
  console.log(`  ${tr(zh, "port:", "端口:")}       ${port}`);
  console.log("");
  console.log(tr(zh,
    "Run `openclaw gateway --force` to activate the plugin.",
    "运行 `openclaw gateway --force` 以激活插件。",
  ));
  console.log("");
  return true;
}

async function setupRemote(
  zh: boolean,
  configPath: string,
  existing: Record<string, unknown> | null,
  q: (prompt: string, def?: string) => Promise<string>,
): Promise<void> {
  console.log("");
  console.log(tr(zh, "── Remote Mode Configuration ──", "── 远程模式配置 ──"));
  console.log("");

  const defaultUrl = existing?.baseUrl ? String(existing.baseUrl) : DEFAULT_REMOTE_URL;
  const defaultApiKey = existing?.apiKey ? String(existing.apiKey) : "";
  const defaultAgentId = existing?.agentId ? String(existing.agentId) : "";

  const baseUrl = await q(tr(zh, "OpenViking server URL", "OpenViking 服务器地址"), defaultUrl);
  const apiKey = await q(tr(zh, "API Key (optional)", "API Key（可选）"), defaultApiKey);
  const agentId = await q(tr(zh, "Agent ID (optional)", "Agent ID（可选）"), defaultAgentId);

  console.log("");

  // Connectivity test (non-blocking)
  console.log(tr(zh, `Testing connectivity to ${baseUrl}...`, `正在测试连接 ${baseUrl}...`));
  const health = await checkServiceHealth(baseUrl, apiKey || undefined);
  if (health.ok) {
    const ver = health.version ? ` (version: ${health.version})` : "";
    console.log(`  ✓ ${tr(zh, `Connected successfully${ver}`, `连接成功${ver}`)}`);
  } else {
    console.log(`  ✗ ${tr(zh, `Connection failed: ${health.error}`, `连接失败: ${health.error}`)}`);
    console.log("");
    console.log(tr(zh,
      "  The configuration will still be saved. Make sure the server is reachable\n  before starting the gateway.",
      "  配置仍会保存。请确保服务器在启动 gateway 前可达。",
    ));
  }
  console.log("");

  // Write config
  const pluginCfg: Record<string, unknown> = {
    ...(existing ?? {}),
    mode: "remote",
    baseUrl,
  };
  if (apiKey) pluginCfg.apiKey = apiKey;
  else delete pluginCfg.apiKey;
  if (agentId) pluginCfg.agentId = agentId;
  else delete pluginCfg.agentId;
  delete pluginCfg.configPath;
  delete pluginCfg.port;

  writeConfig(configPath, pluginCfg);

  console.log("");
  console.log(`  ${tr(zh, "mode:", "模式:")}    remote`);
  console.log(`  baseUrl: ${baseUrl}`);
  if (apiKey) console.log(`  apiKey:  ${maskKey(apiKey)}`);
  if (agentId) console.log(`  agentId: ${agentId}`);
  console.log("");
  console.log(tr(zh,
    "Run `openclaw gateway --force` to activate the plugin.",
    "运行 `openclaw gateway --force` 以激活插件。",
  ));
  console.log("");
}

export const __test__ = {
  parseWindowsEnvBatPythonPath,
  parsePosixEnvPythonPath,
  validateLocalConfigPath,
  setupLocal,
};
