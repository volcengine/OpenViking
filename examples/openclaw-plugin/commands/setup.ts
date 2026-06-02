import * as os from "node:os";
import * as path from "node:path";
import * as readline from "node:readline";
import {
  activateContextEngineSlot,
  defaultSetupIO,
  ensureInstallRecord,
  getExistingPluginConfig,
  isContextEngineSlotActive,
  readOpenClawConfig,
  type SlotActivationResult,
} from "../services/setup/config-writer.js";
import { getEnv } from "../runtime-utils.js";
import {
  createOpenVikingSetupService,
  isLegacyLocalMode,
  maskKey,
  type ApiKeyProbeResult,
  type HealthResult,
  type SetupResult,
  type StatusResult,
} from "../services/setup/setup-flow.js";
import { createSetupNetworkProbes } from "../services/setup/probe-service.js";
import {
  COMPATIBLE_SERVER_MAX,
  COMPATIBLE_SERVER_MIN,
  PLUGIN_VERSION,
} from "../services/setup/package-metadata.js";
import {
  checkVersionCompatibility as checkVersionCompatibilityForRange,
  type VersionCompatibility,
} from "../services/setup/version-compatibility.js";
import { setExitCodeOnFailure } from "../services/setup/exit-utils.js";

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

type RegisterCliArgs = {
  program: CommandProgram;
};

function tr(langZh: boolean, en: string, zh: string): string {
  return langZh ? zh : en;
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

const checkVersionCompatibility = (serverVersion: string): VersionCompatibility =>
  checkVersionCompatibilityForRange(serverVersion, {
    min: COMPATIBLE_SERVER_MIN,
    max: COMPATIBLE_SERVER_MAX,
  });

function formatCompatRange(): string {
  if (COMPATIBLE_SERVER_MIN && COMPATIBLE_SERVER_MAX) return `${COMPATIBLE_SERVER_MIN} ~ ${COMPATIBLE_SERVER_MAX}`;
  if (COMPATIBLE_SERVER_MIN) return `>= ${COMPATIBLE_SERVER_MIN}`;
  if (COMPATIBLE_SERVER_MAX) return `<= ${COMPATIBLE_SERVER_MAX}`;
  return "any";
}

const setupNetwork = createSetupNetworkProbes({
  pluginVersion: PLUGIN_VERSION,
  compatRange: formatCompatRange(),
  checkVersionCompatibility,
});

const probeApiKeyType: (baseUrl: string, apiKey?: string) => Promise<ApiKeyProbeResult> = setupNetwork.probeApiKeyType;
const checkServiceHealth: (baseUrl: string, apiKey?: string) => Promise<HealthResult> = setupNetwork.checkServiceHealth;

function detectLangZh(options: Record<string, unknown>): boolean {
  if (options.zh) return true;
  const lang = getEnv("LANG") || getEnv("LC_ALL") || "";
  return /^zh/i.test(lang);
}

function normalizeSetupRecallTargetTypes(value: unknown): string[] | undefined {
  const entries = Array.isArray(value)
    ? value.flatMap((entry) => String(entry).split(/[,\n]/))
    : typeof value === "string"
      ? value.split(/[,\n]/)
      : [];
  const normalized = entries.map((entry) => entry.trim()).filter(Boolean);
  return normalized.length > 0 ? [...new Set(normalized)] : undefined;
}

const setupService = createOpenVikingSetupService({
  io: defaultSetupIO,
  defaultRemoteUrl: DEFAULT_REMOTE_URL,
  checkServiceHealth,
  probeApiKeyType,
});

const setupNonInteractive = setupService.setupNonInteractive;
const getStatus = setupService.getStatus;
const saveInteractiveRemoteConfig = setupService.saveInteractiveRemoteConfig;
const useExistingRemoteConfig = setupService.useExistingRemoteConfig;

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
        .option("--recall-target-types <types>", "Comma-separated recall target types (for resource-only recall use: resource)")
        .option("--allow-offline", "Allow config write even if server is unreachable")
        .option("--force-slot", "Replace existing contextEngine slot even if owned by another plugin")
        .option("--json", "Output result as JSON (machine-readable)")
        .action(async (...args: unknown[]) => {
          const options = (args[0] ?? {}) as Record<string, unknown>;
          const {
            reconfigure, zh: zhOpt, baseUrl, apiKey, agentPrefix,
            accountId, userId, recallTargetTypes, allowOffline, forceSlot, json: jsonOpt,
          } = options as {
            reconfigure?: boolean; zh?: boolean; baseUrl?: string;
            apiKey?: string; agentPrefix?: string; accountId?: string;
            userId?: string; recallTargetTypes?: string; allowOffline?: boolean; forceSlot?: boolean;
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
              recallTargetTypes: normalizeSetupRecallTargetTypes(recallTargetTypes),
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

              const existingResult = await useExistingRemoteConfig(configPath, existing);
              printRemoteCheckResult(zh, existingResult.config?.baseUrl ?? DEFAULT_REMOTE_URL, existingResult.health);
              printSlotResult(zh, existingResult.slot);

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

function printRemoteCheckResult(
  zh: boolean,
  baseUrl: string,
  health: HealthResult | undefined,
): void {
  console.log(tr(zh, `Testing connectivity to ${baseUrl}...`, `正在测试连接 ${baseUrl}...`));
  if (health?.ok) {
    const ver = health.version ? ` (version: ${health.version})` : "";
    console.log(`  ✓ ${tr(zh, `Connected successfully${ver}`, `连接成功${ver}`)}`);
    printCompatibilityWarning(zh, health);
  } else if (health) {
    console.log(`  ✗ ${tr(zh, `Connection failed: ${health.error}`, `连接失败: ${health.error}`)}`);
  }
  console.log("");
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
      if (result.config.recallTargetTypes) console.log(`  recallTargetTypes: ${result.config.recallTargetTypes.join(",")}`);
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

  const { slot: slotResult } = await saveInteractiveRemoteConfig(configPath, {
    existing,
    baseUrl,
    apiKey,
    agentPrefix,
    accountId,
    userId,
  });

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
