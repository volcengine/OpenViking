import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as readline from "node:readline";
import { launchProcess, sysEnv, getEnv } from "../runtime-utils.js";

const IS_WIN = os.platform() === "win32";

const HOME = os.homedir();
const OPENCLAW_DIR = getEnv("OPENCLAW_STATE_DIR") || path.join(HOME, ".openclaw");
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

function isValidAgentPrefixInput(value: string): boolean {
  const trimmed = value.trim();
  return !trimmed || /^[a-zA-Z0-9_-]+$/.test(trimmed);
}

async function askAgentPrefix(
  zh: boolean,
  q: (prompt: string, def?: string) => Promise<string>,
  defaultValue: string,
): Promise<string> {
  while (true) {
    const value = (await q(
      tr(zh, "Agent Prefix (optional)", "Agent Prefix（可选）"),
      defaultValue,
    )).trim();
    if (isValidAgentPrefixInput(value)) {
      return value;
    }
    console.log(
      `  ✗ ${tr(
        zh,
        "Agent Prefix may only contain letters, digits, underscores, and hyphens, or be empty.",
        "Agent Prefix 只能包含字母、数字、下划线和连字符，或留空。",
      )}`,
    );
  }
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

function isLegacyLocalMode(existing: Record<string, unknown>): boolean {
  const mode = existing.mode;
  return mode !== "remote";
}

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
              if (isLegacyLocalMode(existing)) {
                console.log(tr(
                  zh,
                  "Existing configuration uses local mode, which is no longer supported.",
                  "当前配置为本地模式，已不再支持。",
                ));
                console.log(tr(
                  zh,
                  "Run `openclaw openviking setup --reconfigure` to configure a remote OpenViking server.",
                  "请运行 `openclaw openviking setup --reconfigure` 以配置远程 OpenViking 服务。",
                ));
                console.log("");
                return;
              }

              console.log(tr(zh, "Existing configuration found:", "已找到现有配置："));
              console.log(`  mode:    ${existing.mode}`);
              console.log(`  baseUrl: ${existing.baseUrl ?? DEFAULT_REMOTE_URL}`);
              if (existing.apiKey) console.log(`  apiKey:  ${maskKey(String(existing.apiKey))}`);
              if (existing.agent_prefix) console.log(`  agent_prefix: ${existing.agent_prefix}`);
              console.log("");
              console.log(tr(
                zh,
                "Press Enter to keep existing values, or use --reconfigure to change.",
                "按 Enter 保留现有配置，或使用 --reconfigure 重新配置。",
              ));
              console.log("");
              console.log(tr(zh, "✓ Using existing configuration", "✓ 使用现有配置"));
              console.log("");

              await runRemoteCheck(zh, existing);

              console.log(tr(zh,
                "✓ Plugin is ready. Run `openclaw gateway --force` to activate.",
                "✓ 插件已就绪。运行 `openclaw gateway --force` 以激活。",
              ));
              console.log("");
              return;
            }

            if (existing && options.reconfigure) {
              console.log(tr(zh, "Existing configuration found:", "已找到现有配置："));
              if (isLegacyLocalMode(existing)) {
                console.log(tr(zh,
                  "(Previous local-mode settings will be replaced with remote settings.)",
                  "（将用远程模式设置替换此前的本地模式配置。）",
                ));
              } else {
                console.log(`  mode:    ${existing.mode}`);
                console.log(`  baseUrl: ${existing.baseUrl ?? DEFAULT_REMOTE_URL}`);
                if (existing.apiKey) console.log(`  apiKey:  ${maskKey(String(existing.apiKey))}`);
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

            await setupRemote(zh, configPath, existing, q);
          } finally {
            rl.close();
          }
        });
    },
    { commands: ["openviking"] },
  );
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

async function setupRemote(
  zh: boolean,
  configPath: string,
  existing: Record<string, unknown> | null,
  q: (prompt: string, def?: string) => Promise<string>,
): Promise<void> {
  console.log("");
  console.log(tr(zh, "── Remote Mode Configuration ──", "── 远程模式配置 ──"));
  console.log("");

  const defaultUrl = existing?.baseUrl && String(existing.baseUrl).trim()
    ? String(existing.baseUrl)
    : DEFAULT_REMOTE_URL;
  const defaultApiKey = existing?.apiKey ? String(existing.apiKey) : "";
  const defaultAgentPrefix = existing?.agent_prefix ? String(existing.agent_prefix) : "";

  const baseUrl = await q(tr(zh, "OpenViking server URL", "OpenViking 服务器地址"), defaultUrl);
  const apiKey = await q(tr(zh, "API Key (optional)", "API Key（可选）"), defaultApiKey);
  const agentPrefix = await askAgentPrefix(zh, q, defaultAgentPrefix);

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
  if (agentPrefix) pluginCfg.agent_prefix = agentPrefix;
  else delete pluginCfg.agent_prefix;
  delete pluginCfg.configPath;
  delete pluginCfg.port;

  writeConfig(configPath, pluginCfg);

  console.log("");
  console.log(`  ${tr(zh, "mode:", "模式:")}    remote`);
  console.log(`  baseUrl: ${baseUrl}`);
  if (apiKey) console.log(`  apiKey:  ${maskKey(apiKey)}`);
  if (agentPrefix) console.log(`  agent_prefix: ${agentPrefix}`);
  console.log("");
  console.log(tr(zh,
    "Run `openclaw gateway --force` to activate the plugin.",
    "运行 `openclaw gateway --force` 以激活插件。",
  ));
  console.log("");
}

export const __test__ = {
  resolveAbsoluteCommand,
  isLegacyLocalMode,
  isValidAgentPrefixInput,
};
