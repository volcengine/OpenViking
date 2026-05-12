import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as readline from "node:readline";
import { fileURLToPath } from "node:url";
import { getEnv } from "../runtime-utils.js";

const HOME = os.homedir();
const OPENCLAW_DIR = getEnv("OPENCLAW_STATE_DIR") || path.join(HOME, ".openclaw");
const DEFAULT_REMOTE_URL = "http://127.0.0.1:1933";

function findPluginPackageRoot(fromDir = path.dirname(fileURLToPath(import.meta.url))): string | null {
  let current = path.resolve(fromDir);
  for (let depth = 0; depth < 5; depth += 1) {
    if (
      fs.existsSync(path.join(current, "package.json")) &&
      fs.existsSync(path.join(current, "openclaw.plugin.json"))
    ) {
      return current;
    }

    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return null;
}

function readPluginVersion(): string {
  try {
    const packageRoot = findPluginPackageRoot();
    if (!packageRoot) return "unknown";
    const pkgPath = path.join(packageRoot, "package.json");
    const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf-8"));
    return String(pkg.version ?? "unknown");
  } catch {
    return "unknown";
  }
}

function readCompatRangeFromManifest(): { min: string; max: string } {
  try {
    const packageRoot = findPluginPackageRoot();
    if (!packageRoot) return { min: "", max: "" };
    const manifestPath = path.join(packageRoot, "install-manifest.json");
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    const compat = manifest?.compatibility ?? {};
    return {
      min: String(compat.minOpenvikingVersion ?? ""),
      max: String(compat.maxOpenvikingVersion ?? ""),
    };
  } catch {
    return { min: "", max: "" };
  }
}

const PLUGIN_VERSION = readPluginVersion();
const { min: COMPATIBLE_SERVER_MIN, max: COMPATIBLE_SERVER_MAX } = readCompatRangeFromManifest();

type CommandProgram = {
  command: (name: string) => CommandBuilder;
};

type CommandBuilder = {
  description: (desc: string) => CommandBuilder;
  option: (flags: string, desc: string) => CommandBuilder;
  command: (name: string) => CommandBuilder;
  action: (fn: (...args: unknown[]) => void | Promise<void>) => CommandBuilder;
};

type RegisterCliArgs = {
  program: CommandProgram;
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

type VersionCompatibility = "compatible" | "server_too_old" | "server_too_new" | "unknown";

type HealthResult = {
  ok: boolean;
  version: string;
  error: string;
  compatibility: VersionCompatibility;
  pluginVersion: string;
  compatRange: string;
};

function parseVersionTuple(v: string): number[] | null {
  const cleaned = v.replace(/^v/i, "").split("-")[0];
  const parts = cleaned.split(".").map(Number);
  if (parts.some(isNaN)) return null;
  return parts;
}

function compareVersions(a: number[], b: number[]): number {
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

function checkVersionCompatibility(serverVersion: string): VersionCompatibility {
  if (!serverVersion) return "unknown";
  const sv = parseVersionTuple(serverVersion);
  if (!sv) return "unknown";

  if (COMPATIBLE_SERVER_MIN) {
    const minV = parseVersionTuple(COMPATIBLE_SERVER_MIN);
    if (minV && compareVersions(sv, minV) < 0) return "server_too_old";
  }
  if (COMPATIBLE_SERVER_MAX) {
    const maxV = parseVersionTuple(COMPATIBLE_SERVER_MAX);
    if (maxV && compareVersions(sv, maxV) > 0) return "server_too_new";
  }
  return "compatible";
}

function formatCompatRange(): string {
  if (COMPATIBLE_SERVER_MIN && COMPATIBLE_SERVER_MAX) return `${COMPATIBLE_SERVER_MIN} ~ ${COMPATIBLE_SERVER_MAX}`;
  if (COMPATIBLE_SERVER_MIN) return `>= ${COMPATIBLE_SERVER_MIN}`;
  if (COMPATIBLE_SERVER_MAX) return `<= ${COMPATIBLE_SERVER_MAX}`;
  return "any";
}

type ApiKeyProbeResult = {
  keyType: "user_key" | "root_key" | "no_key" | "unknown";
  needsAccountId: boolean;
  needsUserId: boolean;
  detail: string;
};

async function probeApiKeyType(baseUrl: string, apiKey?: string): Promise<ApiKeyProbeResult> {
  if (!apiKey) return { keyType: "no_key", needsAccountId: false, needsUserId: false, detail: "No API key configured" };

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10_000);
  const sessionsUrl = `${baseUrl.replace(/\/+$/, "")}/api/v1/sessions?limit=1`;
  try {
    const headers: Record<string, string> = { "X-API-Key": apiKey };
    const response = await fetch(sessionsUrl, {
      headers,
      signal: controller.signal,
    });

    if (response.ok) {
      return { keyType: "user_key", needsAccountId: false, needsUserId: false, detail: "API key has full user context" };
    }

    // Server raises InvalidArgumentError (-> HTTP 400) when a ROOT key calls
    // tenant-scoped endpoints without X-OpenViking-Account / X-OpenViking-User.
    // 401/403 may also be returned by older versions, FastAPI validation can
    // surface as 422. Treat all four as candidates for tenant-context errors.
    if ([400, 401, 403, 422].includes(response.status)) {
      let body = "";
      try {
        body = await response.text();
      } catch { /* ignore parse errors */ }
      const lower = body.toLowerCase();
      const needsAccount = /x-openviking-account|account[_ ]?id|account context|tenant/.test(lower);
      const needsUser = /x-openviking-user|user[_ ]?id|user context|user key/.test(lower);
      if (needsAccount || needsUser) {
        return {
          keyType: "root_key",
          needsAccountId: needsAccount,
          needsUserId: needsUser,
          detail: body.slice(0, 200),
        };
      }

      // Body did not name account/user/tenant explicitly (custom auth middleware,
      // localized message, etc.). Re-probe with placeholder tenant headers; if
      // the failure was due to missing tenant headers the response will change.
      try {
        const probeHeaders: Record<string, string> = {
          "X-API-Key": apiKey,
          "X-OpenViking-Account": "__probe__",
          "X-OpenViking-User": "__probe__",
        };
        const probe2 = await fetch(sessionsUrl, {
          headers: probeHeaders,
          signal: controller.signal,
        });
        if (probe2.status !== response.status) {
          return {
            keyType: "root_key",
            needsAccountId: true,
            needsUserId: true,
            detail: body.slice(0, 200) || `HTTP ${response.status} -> ${probe2.status} after adding tenant headers`,
          };
        }
      } catch { /* ignore probe errors, fall through to unknown */ }

      if (response.status === 401 || response.status === 403) {
        return { keyType: "unknown", needsAccountId: false, needsUserId: false, detail: `HTTP ${response.status} - authentication failed, verify your API key` };
      }
      return { keyType: "unknown", needsAccountId: false, needsUserId: false, detail: `HTTP ${response.status}${body ? ` - ${body.slice(0, 160)}` : ""}` };
    }

    return { keyType: "unknown", needsAccountId: false, needsUserId: false, detail: `HTTP ${response.status}` };
  } catch (err) {
    return { keyType: "unknown", needsAccountId: false, needsUserId: false, detail: String(err instanceof Error ? err.message : err) };
  } finally {
    clearTimeout(timeoutId);
  }
}

async function checkServiceHealth(baseUrl: string, apiKey?: string): Promise<HealthResult> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10_000);
  try {
    const headers: Record<string, string> = {};
    if (apiKey) headers["X-API-Key"] = apiKey;
    const response = await fetch(`${baseUrl.replace(/\/+$/, "")}/health`, {
      headers,
      signal: controller.signal,
    });
    if (response.ok) {
      try {
        const data = await response.json() as Record<string, unknown>;
        const result = (data.result ?? data) as Record<string, unknown>;
        const version = String(result.version ?? data.version ?? "");
        const compatibility = checkVersionCompatibility(version);
        return { ok: true, version, error: "", compatibility, pluginVersion: PLUGIN_VERSION, compatRange: formatCompatRange() };
      } catch {
        return { ok: true, version: "", error: "", compatibility: "unknown", pluginVersion: PLUGIN_VERSION, compatRange: formatCompatRange() };
      }
    }
    return { ok: false, version: "", error: `HTTP ${response.status}`, compatibility: "unknown", pluginVersion: PLUGIN_VERSION, compatRange: formatCompatRange() };
  } catch (err) {
    return { ok: false, version: "", error: String(err instanceof Error ? err.message : err), compatibility: "unknown", pluginVersion: PLUGIN_VERSION, compatRange: formatCompatRange() };
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

function backupConfig(configPath: string): string | null {
  if (!fs.existsSync(configPath)) return null;
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const backupPath = `${configPath}.bak.${timestamp}`;
  try {
    fs.copyFileSync(configPath, backupPath);
    return backupPath;
  } catch {
    return null;
  }
}

function ensureInstallRecord(plugins: Record<string, unknown>): void {
  const installs = plugins.installs as Record<string, unknown> | undefined;
  if (installs && typeof installs === "object") {
    delete installs.openviking;
  }

  if (!plugins.allow) plugins.allow = [];
  const allow = plugins.allow as string[];
  if (!allow.includes("openviking")) {
    allow.push("openviking");
  }
}

function writeConfig(
  configPath: string,
  pluginCfg: Record<string, unknown>,
): void {
  const configDir = path.dirname(configPath);
  if (!fs.existsSync(configDir)) fs.mkdirSync(configDir, { recursive: true });

  backupConfig(configPath);

  const config = readOpenClawConfig(configPath);

  if (!config.plugins) config.plugins = {};
  const plugins = config.plugins as Record<string, unknown>;
  if (!plugins.entries) plugins.entries = {};
  const entries = plugins.entries as Record<string, unknown>;

  const existingEntry = (entries.openviking as Record<string, unknown>) ?? {};
  entries.openviking = { ...existingEntry, config: pluginCfg };

  ensureInstallRecord(plugins);

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

type SetupResult = {
  success: boolean;
  action: "configured" | "existing" | "error" | "slot_blocked";
  config?: {
    mode: string;
    baseUrl: string;
    apiKey?: string;
    agent_prefix?: string;
    accountId?: string;
    userId?: string;
  };
  health?: HealthResult;
  keyProbe?: ApiKeyProbeResult;
  slot: SlotActivationResult;
  error?: string;
};

function setExitCodeOnFailure(result: { success: boolean }): void {
  if (!result.success) {
    process.exitCode = 1;
  }
}

type StatusResult = {
  configured: boolean;
  config?: {
    mode: string;
    baseUrl: string;
    hasApiKey: boolean;
    agent_prefix?: string;
    hasAccountId: boolean;
    hasUserId: boolean;
  };
  health?: HealthResult;
  keyProbe?: ApiKeyProbeResult;
  slotActive: boolean;
};

type SlotActivationResult = {
  activated: boolean;
  previousOwner?: string;
  replaced: boolean;
};

function activateContextEngineSlot(configPath: string, force = false): SlotActivationResult {
  const config = readOpenClawConfig(configPath);
  if (!config.plugins) config.plugins = {};
  const plugins = config.plugins as Record<string, unknown>;
  if (!plugins.slots) plugins.slots = {};
  const slots = plugins.slots as Record<string, unknown>;

  const current = slots.contextEngine as string | undefined;

  if (current === "openviking") return { activated: false, replaced: false };

  if (current && current !== "openviking" && !force) {
    return { activated: false, previousOwner: current, replaced: false };
  }

  slots.contextEngine = "openviking";
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n", "utf-8");
  return { activated: true, previousOwner: current || undefined, replaced: !!current };
}

function isContextEngineSlotActive(configPath: string): boolean {
  const config = readOpenClawConfig(configPath);
  const plugins = config.plugins as Record<string, unknown> | undefined;
  if (!plugins) return false;
  const slots = plugins.slots as Record<string, unknown> | undefined;
  return slots?.contextEngine === "openviking";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function registerSetupCli(api: any): void {
  if (!api.registerCli) {
    api.logger.info("openviking: registerCli not available, setup command skipped");
    return;
  }

  api.registerCli(
    ({ program }: RegisterCliArgs) => {
      const ovCmd = program.command("openviking").description("OpenViking plugin commands");

      ovCmd
        .command("setup")
        .description("Setup OpenViking plugin (supports both interactive and non-interactive modes)")
        .option("--reconfigure", "Force re-entry of all configuration values")
        .option("--zh", "Chinese prompts")
        .option("--base-url <url>", "OpenViking server URL (enables non-interactive mode)")
        .option("--api-key <key>", "API key for authentication")
        .option("--agent-prefix <prefix>", "Agent routing prefix for namespace isolation")
        .option("--account-id <id>", "Account ID (required for root API keys)")
        .option("--user-id <id>", "User ID (required for root API keys)")
        .option("--allow-offline", "Allow config write even if server is unreachable")
        .option("--force-slot", "Replace existing contextEngine slot even if owned by another plugin")
        .option("--json", "Output result as JSON (machine-readable)")
        .action(async (...args: unknown[]) => {
          const options = (args[0] ?? {}) as Record<string, unknown>;
          const {
            reconfigure, zh: zhOpt, baseUrl, apiKey, agentPrefix,
            accountId, userId, allowOffline, forceSlot, json: jsonOpt,
          } = options as {
            reconfigure?: boolean; zh?: boolean; baseUrl?: string;
            apiKey?: string; agentPrefix?: string; accountId?: string;
            userId?: string; allowOffline?: boolean; forceSlot?: boolean;
            json?: boolean;
          };
          const zh = detectLangZh(options);
          const configPath = path.join(OPENCLAW_DIR, "openclaw.json");
          const jsonMode = !!jsonOpt;
          const nonInteractive = !!baseUrl;

          if (nonInteractive) {
            const result = await setupNonInteractive(configPath, {
              baseUrl: baseUrl!,
              apiKey,
              agentPrefix,
              accountId,
              userId,
              allowOffline: !!allowOffline,
              forceSlot: !!forceSlot,
            });
            if (jsonMode) {
              console.log(JSON.stringify(result, null, 2));
            } else {
              printSetupResult(zh, result);
            }
            setExitCodeOnFailure(result);
            return;
          }

          if (jsonMode && !nonInteractive) {
            const result: SetupResult = {
              success: false,
              action: "error",
              slot: { activated: false, replaced: false },
              error: "--json requires --base-url for non-interactive mode",
            };
            console.log(JSON.stringify(result, null, 2));
            setExitCodeOnFailure(result);
            return;
          }

          console.log("");
          console.log(`🦣 ${tr(zh, "OpenViking Plugin Setup", "OpenViking 插件配置向导")}`);
          console.log("");

          const config = readOpenClawConfig(configPath);
          const existing = getExistingPluginConfig(config);

          const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
          const q = (prompt: string, def = "") => ask(rl, prompt, def);

          try {
            if (existing && !reconfigure) {
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

              const slotResult = activateContextEngineSlot(configPath);
              printSlotResult(zh, slotResult);

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

      ovCmd
        .command("status")
        .description("Show current OpenViking plugin status and connectivity")
        .option("--zh", "Chinese prompts")
        .option("--json", "Output result as JSON (machine-readable)")
        .action(async (...args: unknown[]) => {
          const options = (args[0] ?? {}) as Record<string, unknown>;
          const { zh: zhOpt, json: jsonOpt } = options as { zh?: boolean; json?: boolean };
          const zh = detectLangZh(options);
          const configPath = path.join(OPENCLAW_DIR, "openclaw.json");
          const jsonMode = !!jsonOpt;

          const result = await getStatus(configPath);

          if (jsonMode) {
            console.log(JSON.stringify(result, null, 2));
            return;
          }

          printStatus(zh, result);
        });
    },
    { commands: ["openviking"] },
  );
}

function printCompatibilityWarning(zh: boolean, health: HealthResult): void {
  if (health.compatibility === "server_too_old") {
    console.log(`  ⚠ ${tr(zh,
      `Server version ${health.version} is older than recommended (${health.compatRange}). Some features may not work. Please upgrade OpenViking server.`,
      `服务端版本 ${health.version} 低于推荐范围（${health.compatRange}）。部分功能可能不可用，请升级 OpenViking 服务端。`,
    )}`);
  } else if (health.compatibility === "server_too_new") {
    console.log(`  ⚠ ${tr(zh,
      `Server version ${health.version} is newer than supported (${health.compatRange}). Please upgrade the OpenViking plugin.`,
      `服务端版本 ${health.version} 高于插件支持范围（${health.compatRange}）。请升级 OpenViking 插件。`,
    )}`);
  } else if (health.compatibility === "unknown" && health.ok) {
    console.log(`  ⚠ ${tr(zh,
      "Could not determine server version. Compatibility check skipped.",
      "无法获取服务端版本，已跳过兼容性检查。",
    )}`);
  }
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
    printCompatibilityWarning(zh, health);
  } else {
    console.log(`  ✗ ${tr(zh, `Connection failed: ${health.error}`, `连接失败: ${health.error}`)}`);
  }
  console.log("");
}

async function setupNonInteractive(
  configPath: string,
  params: { baseUrl: string; apiKey?: string; agentPrefix?: string; accountId?: string; userId?: string; allowOffline?: boolean; forceSlot?: boolean },
): Promise<SetupResult> {
  try {
    const { baseUrl, apiKey, agentPrefix, accountId, userId, allowOffline, forceSlot } = params;

    // Phase 1: validate connectivity and key type BEFORE writing config
    const health = await checkServiceHealth(baseUrl, apiKey);

    if (!health.ok && !allowOffline) {
      return {
        success: false,
        action: "error",
        config: { mode: "remote", baseUrl },
        health,
        slot: { activated: false, replaced: false },
        error: `Server unreachable: ${health.error}. Use --allow-offline to save config anyway.`,
      };
    }

    const keyProbe = health.ok ? await probeApiKeyType(baseUrl, apiKey) : undefined;

    if (keyProbe?.keyType === "root_key" && (!accountId || !userId)) {
      const missing: string[] = [];
      if (!accountId) missing.push("--account-id");
      if (!userId) missing.push("--user-id");
      return {
        success: false,
        action: "error",
        config: {
          mode: "remote",
          baseUrl,
          ...(apiKey ? { apiKey: maskKey(apiKey) } : {}),
        },
        health,
        keyProbe,
        slot: { activated: false, replaced: false },
        error: `Root API key detected. Missing: ${missing.join(", ")}. Re-run with: ${missing.map(f => `${f} <value>`).join(" ")}`,
      };
    }

    // Phase 2: all checks passed (or --allow-offline), write config and activate slot
    const pluginCfg: Record<string, unknown> = { mode: "remote", baseUrl };
    if (apiKey) pluginCfg.apiKey = apiKey;
    if (agentPrefix) pluginCfg.agent_prefix = agentPrefix;
    if (accountId) pluginCfg.accountId = accountId;
    if (userId) pluginCfg.userId = userId;

    writeConfig(configPath, pluginCfg);
    const slot = activateContextEngineSlot(configPath, !!forceSlot);

    if (!slot.activated && slot.previousOwner) {
      return {
        success: false,
        action: "slot_blocked",
        config: {
          mode: "remote",
          baseUrl,
          ...(apiKey ? { apiKey: maskKey(apiKey) } : {}),
          ...(agentPrefix ? { agent_prefix: agentPrefix } : {}),
          ...(accountId ? { accountId } : {}),
          ...(userId ? { userId } : {}),
        },
        health,
        keyProbe,
        slot,
        error: `contextEngine slot is owned by "${slot.previousOwner}". Config was saved but slot was NOT changed. Use --force-slot to replace.`,
      };
    }

    return {
      success: true,
      action: "configured",
      config: {
        mode: "remote",
        baseUrl,
        ...(apiKey ? { apiKey: maskKey(apiKey) } : {}),
        ...(agentPrefix ? { agent_prefix: agentPrefix } : {}),
        ...(accountId ? { accountId } : {}),
        ...(userId ? { userId } : {}),
      },
      health,
      keyProbe,
      slot,
    };
  } catch (err) {
    return {
      success: false,
      action: "error",
      slot: { activated: false, replaced: false },
      error: String(err instanceof Error ? err.message : err),
    };
  }
}

function printSetupResult(zh: boolean, result: SetupResult): void {
  console.log("");
  if (result.success) {
    console.log(`🦣 ${tr(zh, "OpenViking Plugin Setup Complete", "OpenViking 插件配置完成")}`);
    console.log("");
    if (result.config) {
      console.log(`  mode:    ${result.config.mode}`);
      console.log(`  baseUrl: ${result.config.baseUrl}`);
      if (result.config.apiKey) console.log(`  apiKey:  ${result.config.apiKey}`);
      if (result.config.agent_prefix) console.log(`  agent_prefix: ${result.config.agent_prefix}`);
      if (result.config.accountId) console.log(`  accountId: ${result.config.accountId}`);
      if (result.config.userId) console.log(`  userId:  ${result.config.userId}`);
    }
    console.log("");
    if (result.health?.ok) {
      const ver = result.health.version ? ` (version: ${result.health.version})` : "";
      console.log(`  ✓ ${tr(zh, `Connected successfully${ver}`, `连接成功${ver}`)}`);
      printCompatibilityWarning(zh, result.health);
    } else if (result.health) {
      console.log(`  ✗ ${tr(zh, `Connection failed: ${result.health.error}`, `连接失败: ${result.health.error}`)}`);
    }
    if (result.keyProbe) {
      printKeyProbeWarning(zh, result.keyProbe);
    }
    printSlotResult(zh, result.slot);
    console.log("");
    console.log(tr(zh,
      "Run `openclaw gateway --force` to activate the plugin.",
      "运行 `openclaw gateway --force` 以激活插件。",
    ));
  } else {
    console.log(`✗ ${tr(zh, "Setup failed", "配置失败")}: ${result.error}`);
    if (result.keyProbe?.keyType === "root_key") {
      printKeyProbeWarning(zh, result.keyProbe);
    }
  }
  console.log("");
}

async function getStatus(configPath: string): Promise<StatusResult> {
  const config = readOpenClawConfig(configPath);
  const existing = getExistingPluginConfig(config);
  const slotActive = isContextEngineSlotActive(configPath);

  if (!existing) {
    return { configured: false, slotActive };
  }

  const baseUrl = String(existing.baseUrl ?? DEFAULT_REMOTE_URL);
  const apiKey = existing.apiKey ? String(existing.apiKey) : undefined;
  const health = await checkServiceHealth(baseUrl, apiKey);
  const keyProbe = health.ok ? await probeApiKeyType(baseUrl, apiKey) : undefined;

  const agentPrefix = existing.agent_prefix ?? existing.agentId;
  return {
    configured: true,
    config: {
      mode: String(existing.mode ?? "remote"),
      baseUrl,
      hasApiKey: !!existing.apiKey,
      ...(agentPrefix ? { agent_prefix: String(agentPrefix) } : {}),
      hasAccountId: !!existing.accountId,
      hasUserId: !!existing.userId,
    },
    health,
    keyProbe,
    slotActive,
  };
}

function printSlotResult(zh: boolean, slot: SlotActivationResult): void {
  if (slot.activated && slot.replaced) {
    console.log(`  ⚠ ${tr(zh,
      `Replaced context-engine slot: ${slot.previousOwner} → openviking`,
      `已替换 context-engine 插槽: ${slot.previousOwner} → openviking`,
    )}`);
  } else if (slot.activated) {
    console.log(`  ✓ ${tr(zh, "Activated context-engine slot: openviking", "已激活 context-engine 插槽: openviking")}`);
  } else if (slot.previousOwner && slot.previousOwner !== "openviking") {
    console.log(`  ⚠ ${tr(zh,
      `Context-engine slot is owned by "${slot.previousOwner}". Run: openclaw config set plugins.slots.contextEngine openviking`,
      `context-engine 插槽当前由 "${slot.previousOwner}" 占用。运行: openclaw config set plugins.slots.contextEngine openviking`,
    )}`);
  }
}

function printKeyProbeWarning(zh: boolean, probe: ApiKeyProbeResult): void {
  if (probe.keyType === "root_key") {
    console.log(`  ⚠ ${tr(zh,
      "Root API key detected. accountId and userId are required for this key type.",
      "检测到 Root API Key，此类型密钥需要提供 accountId 和 userId。",
    )}`);
    if (probe.needsAccountId) {
      console.log(`    ${tr(zh,
        "→ Missing: accountId (use --account-id or set in config)",
        "→ 缺少: accountId（使用 --account-id 或在配置中设置）",
      )}`);
    }
    if (probe.needsUserId) {
      console.log(`    ${tr(zh,
        "→ Missing: userId (use --user-id or set in config)",
        "→ 缺少: userId（使用 --user-id 或在配置中设置）",
      )}`);
    }
  }
}

function printStatus(zh: boolean, result: StatusResult): void {
  console.log("");
  console.log(`🦣 ${tr(zh, "OpenViking Plugin Status", "OpenViking 插件状态")}`);
  console.log("");

  if (!result.configured) {
    console.log(`  ${tr(zh, "Status: Not configured", "状态: 未配置")}`);
    console.log(`  ${tr(zh, "Run `openclaw openviking setup` to configure.", "运行 `openclaw openviking setup` 进行配置。")}`);
    console.log("");
    return;
  }

  console.log(`  ${tr(zh, "Status: Configured", "状态: 已配置")}`);
  if (result.config) {
    console.log(`  mode:      ${result.config.mode}`);
    console.log(`  baseUrl:   ${result.config.baseUrl}`);
    console.log(`  apiKey:    ${result.config.hasApiKey ? "set" : "not set"}`);
    if (result.config.agent_prefix) console.log(`  agent_prefix: ${result.config.agent_prefix}`);
    console.log(`  accountId: ${result.config.hasAccountId ? "set" : "not set"}`);
    console.log(`  userId:    ${result.config.hasUserId ? "set" : "not set"}`);
  }
  console.log(`  slot:      ${result.slotActive ? "active" : "inactive"}`);
  console.log("");

  if (result.health?.ok) {
    const ver = result.health.version ? ` (version: ${result.health.version})` : "";
    console.log(`  ✓ ${tr(zh, `Server reachable${ver}`, `服务器可达${ver}`)}`);
    printCompatibilityWarning(zh, result.health);
  } else if (result.health) {
    console.log(`  ✗ ${tr(zh, `Server unreachable: ${result.health.error}`, `服务器不可达: ${result.health.error}`)}`);
  }

  if (result.keyProbe) {
    printKeyProbeWarning(zh, result.keyProbe);
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

  let accountId = existing?.accountId ? String(existing.accountId) : "";
  let userId = existing?.userId ? String(existing.userId) : "";

  if (apiKey) {
    console.log(tr(zh, "  Detecting API key type...", "  正在检测 API Key 类型..."));
    const probe = await probeApiKeyType(baseUrl, apiKey);
    if (probe.keyType === "root_key") {
      console.log(tr(zh,
        "  ⚠ Root API key detected. accountId and userId are required.",
        "  ⚠ 检测到 Root API Key，需要提供 accountId 和 userId。",
      ));
      accountId = await q(tr(zh, "Account ID (required for root key)", "Account ID（root key 必填）"), accountId);
      userId = await q(tr(zh, "User ID (required for root key)", "User ID（root key 必填）"), userId);
    } else if (probe.keyType === "user_key") {
      console.log(tr(zh, "  ✓ User key verified", "  ✓ User key 已验证"));
    }
  }

  const agentPrefix = await askAgentPrefix(zh, q, defaultAgentPrefix);

  console.log("");

  console.log(tr(zh, `Testing connectivity to ${baseUrl}...`, `正在测试连接 ${baseUrl}...`));
  const health = await checkServiceHealth(baseUrl, apiKey || undefined);
  if (health.ok) {
    const ver = health.version ? ` (version: ${health.version})` : "";
    console.log(`  ✓ ${tr(zh, `Connected successfully${ver}`, `连接成功${ver}`)}`);
    printCompatibilityWarning(zh, health);
  } else {
    console.log(`  ✗ ${tr(zh, `Connection failed: ${health.error}`, `连接失败: ${health.error}`)}`);
    console.log("");
    console.log(tr(zh,
      "  The configuration will still be saved. Make sure the server is reachable\n  before starting the gateway.",
      "  配置仍会保存。请确保服务器在启动 gateway 前可达。",
    ));
  }
  console.log("");

  const pluginCfg: Record<string, unknown> = {
    ...(existing ?? {}),
    mode: "remote",
    baseUrl,
  };
  if (apiKey) pluginCfg.apiKey = apiKey;
  else delete pluginCfg.apiKey;
  if (agentPrefix) pluginCfg.agent_prefix = agentPrefix;
  else delete pluginCfg.agent_prefix;
  if (accountId) pluginCfg.accountId = accountId;
  else delete pluginCfg.accountId;
  if (userId) pluginCfg.userId = userId;
  else delete pluginCfg.userId;
  delete pluginCfg.configPath;
  delete pluginCfg.port;

  writeConfig(configPath, pluginCfg);

  const slotResult = activateContextEngineSlot(configPath);

  console.log("");
  console.log(`  ${tr(zh, "mode:", "模式:")}    remote`);
  console.log(`  baseUrl: ${baseUrl}`);
  if (apiKey) console.log(`  apiKey:  ${maskKey(apiKey)}`);
  if (agentPrefix) console.log(`  agent_prefix: ${agentPrefix}`);
  if (accountId) console.log(`  accountId: ${accountId}`);
  if (userId) console.log(`  userId:  ${userId}`);
  printSlotResult(zh, slotResult);
  console.log("");
  console.log(tr(zh,
    "Run `openclaw gateway --force` to activate the plugin.",
    "运行 `openclaw gateway --force` 以激活插件。",
  ));
  console.log("");
}

export const __test__ = {
  isLegacyLocalMode,
  isValidAgentPrefixInput,
  activateContextEngineSlot,
  isContextEngineSlotActive,
  getStatus,
  setupNonInteractive,
  checkVersionCompatibility,
  parseVersionTuple,
  compareVersions,
  probeApiKeyType,
  ensureInstallRecord,
  findPluginPackageRoot,
  readCompatRangeFromManifest,
  readPluginVersion,
  setExitCodeOnFailure,
};
