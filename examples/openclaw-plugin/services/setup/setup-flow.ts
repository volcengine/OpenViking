import {
  activateContextEngineSlot,
  getExistingPluginConfig,
  isContextEngineSlotActive,
  writeOpenVikingConfig,
  type SetupIO,
  type SlotActivationResult,
} from "./config-writer.js";

export type { SetupIO, SlotActivationResult };

export type VersionCompatibility = "compatible" | "server_too_old" | "server_too_new" | "unknown";

export type HealthResult = {
  ok: boolean;
  version: string;
  error: string;
  compatibility: VersionCompatibility;
  pluginVersion: string;
  compatRange: string;
};

export type ApiKeyProbeResult = {
  keyType: "user_key" | "root_key" | "no_key" | "unknown";
  needsAccountId: boolean;
  needsUserId: boolean;
  detail: string;
};

export type SetupResult = {
  success: boolean;
  action: "configured" | "existing" | "error" | "slot_blocked";
  config?: {
    mode: string;
    baseUrl: string;
    apiKey?: string;
    peer_role?: "none" | "assistant" | "person";
    peer_prefix?: string;
    accountId?: string;
    userId?: string;
    recallTargetTypes?: string[];
  };
  health?: HealthResult;
  keyProbe?: ApiKeyProbeResult;
  slot: SlotActivationResult;
  error?: string;
};

export type StatusResult = {
  configured: boolean;
  config?: {
    mode: string;
    baseUrl: string;
    hasApiKey: boolean;
    peer_role?: "none" | "assistant" | "person";
    peer_prefix?: string;
    hasAccountId: boolean;
    hasUserId: boolean;
  };
  health?: HealthResult;
  keyProbe?: ApiKeyProbeResult;
  slotActive: boolean;
};

export type SetupParams = {
  baseUrl: string;
  apiKey?: string;
  peerRole?: "none" | "assistant" | "person";
  peerPrefix?: string;
  accountId?: string;
  userId?: string;
  recallTargetTypes?: string[];
  allowOffline?: boolean;
  forceSlot?: boolean;
};

export type InteractiveRemoteConfigParams = {
  existing?: Record<string, unknown> | null;
  baseUrl: string;
  apiKey?: string;
  peerRole?: "none" | "assistant" | "person";
  peerPrefix?: string;
  accountId?: string;
  userId?: string;
  forceSlot?: boolean;
};

export type InteractiveRemoteConfigResult = {
  config: Record<string, unknown>;
  slot: SlotActivationResult;
};

export type OpenVikingSetupService = {
  setupNonInteractive: (configPath: string, params: SetupParams) => Promise<SetupResult>;
  saveInteractiveRemoteConfig: (configPath: string, params: InteractiveRemoteConfigParams) => Promise<InteractiveRemoteConfigResult>;
  useExistingRemoteConfig: (configPath: string, existing: Record<string, unknown>) => Promise<SetupResult>;
  getStatus: (configPath: string) => Promise<StatusResult>;
};

export type OpenVikingSetupServiceDependencies = {
  io: SetupIO;
  defaultRemoteUrl?: string;
  checkServiceHealth: (baseUrl: string, apiKey?: string) => Promise<HealthResult>;
  probeApiKeyType: (baseUrl: string, apiKey?: string) => Promise<ApiKeyProbeResult>;
};

export function maskKey(key: string): string {
  if (key.length <= 8) return "****";
  return `${key.slice(0, 4)}...${key.slice(-4)}`;
}

export function isLegacyLocalMode(existing: Record<string, unknown>): boolean {
  const mode = existing.mode;
  return mode !== "remote";
}

export function buildInteractiveRemotePluginConfig({
  existing,
  baseUrl,
  apiKey,
  peerRole,
  peerPrefix,
  accountId,
  userId,
}: InteractiveRemoteConfigParams): Record<string, unknown> {
  const pluginCfg: Record<string, unknown> = {
    ...(existing ?? {}),
    mode: "remote",
    baseUrl,
  };
  if (apiKey) pluginCfg.apiKey = apiKey;
  else delete pluginCfg.apiKey;
  if (peerRole) pluginCfg.peer_role = peerRole;
  else delete pluginCfg.peer_role;
  if (peerPrefix) pluginCfg.peer_prefix = peerPrefix;
  else delete pluginCfg.peer_prefix;
  if (accountId) pluginCfg.accountId = accountId;
  else delete pluginCfg.accountId;
  if (userId) pluginCfg.userId = userId;
  else delete pluginCfg.userId;
  delete pluginCfg.configPath;
  delete pluginCfg.port;
  return pluginCfg;
}

export function createOpenVikingSetupService({
  io,
  defaultRemoteUrl = "http://127.0.0.1:1933",
  checkServiceHealth,
  probeApiKeyType,
}: OpenVikingSetupServiceDependencies): OpenVikingSetupService {
  return {
    async setupNonInteractive(configPath: string, params: SetupParams): Promise<SetupResult> {
      try {
        const { baseUrl, apiKey, peerRole, peerPrefix, accountId, userId, recallTargetTypes, allowOffline, forceSlot } = params;

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

        const pluginCfg: Record<string, unknown> = { mode: "remote", baseUrl };
        if (apiKey) pluginCfg.apiKey = apiKey;
        if (peerRole) pluginCfg.peer_role = peerRole;
        if (peerPrefix) pluginCfg.peer_prefix = peerPrefix;
        if (accountId) pluginCfg.accountId = accountId;
        if (userId) pluginCfg.userId = userId;
        if (recallTargetTypes && recallTargetTypes.length > 0) pluginCfg.recallTargetTypes = recallTargetTypes;

        writeOpenVikingConfig(configPath, pluginCfg, io);
        const slot = activateContextEngineSlot(configPath, !!forceSlot, io);

        const resultConfig = {
          mode: "remote",
          baseUrl,
          ...(apiKey ? { apiKey: maskKey(apiKey) } : {}),
          ...(peerRole ? { peer_role: peerRole } : {}),
          ...(peerPrefix ? { peer_prefix: peerPrefix } : {}),
          ...(accountId ? { accountId } : {}),
          ...(userId ? { userId } : {}),
          ...(recallTargetTypes && recallTargetTypes.length > 0 ? { recallTargetTypes } : {}),
        };

        if (!slot.activated && slot.previousOwner) {
          return {
            success: false,
            action: "slot_blocked",
            config: resultConfig,
            health,
            keyProbe,
            slot,
            error: `contextEngine slot is owned by "${slot.previousOwner}". Config was saved but slot was NOT changed. Use --force-slot to replace.`,
          };
        }

        return {
          success: true,
          action: "configured",
          config: resultConfig,
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
    },

    async saveInteractiveRemoteConfig(
      configPath: string,
      params: InteractiveRemoteConfigParams,
    ): Promise<InteractiveRemoteConfigResult> {
      const pluginCfg = buildInteractiveRemotePluginConfig(params);
      writeOpenVikingConfig(configPath, pluginCfg, io);
      const slot = activateContextEngineSlot(configPath, !!params.forceSlot, io);
      return { config: pluginCfg, slot };
    },

    async useExistingRemoteConfig(configPath: string, existing: Record<string, unknown>): Promise<SetupResult> {
      const baseUrl = String(existing.baseUrl ?? defaultRemoteUrl);
      const apiKey = existing.apiKey ? String(existing.apiKey) : undefined;
      const health = await checkServiceHealth(baseUrl, apiKey);
      const slot = activateContextEngineSlot(configPath, false, io);
      return {
        success: true,
        action: "existing",
        config: {
          mode: String(existing.mode ?? "remote"),
          baseUrl,
          ...(apiKey ? { apiKey: maskKey(apiKey) } : {}),
          ...(existing.peer_role ? { peer_role: String(existing.peer_role) as "none" | "assistant" | "person" } : {}),
          ...(existing.peer_prefix ? { peer_prefix: String(existing.peer_prefix) } : {}),
          ...(existing.accountId ? { accountId: String(existing.accountId) } : {}),
          ...(existing.userId ? { userId: String(existing.userId) } : {}),
        },
        health,
        slot,
      };
    },

    async getStatus(configPath: string): Promise<StatusResult> {
      const config = io.readConfig(configPath);
      const existing = getExistingPluginConfig(config);
      const slotActive = isContextEngineSlotActive(configPath, io);

      if (!existing) {
        return { configured: false, slotActive };
      }

      const baseUrl = String(existing.baseUrl ?? defaultRemoteUrl);
      const apiKey = existing.apiKey ? String(existing.apiKey) : undefined;
      const health = await checkServiceHealth(baseUrl, apiKey);
      const keyProbe = health.ok ? await probeApiKeyType(baseUrl, apiKey) : undefined;

      return {
        configured: true,
        config: {
          mode: String(existing.mode ?? "remote"),
          baseUrl,
          hasApiKey: !!existing.apiKey,
          ...(existing.peer_role ? { peer_role: String(existing.peer_role) as "none" | "assistant" | "person" } : {}),
          ...(existing.peer_prefix ? { peer_prefix: String(existing.peer_prefix) } : {}),
          hasAccountId: !!existing.accountId,
          hasUserId: !!existing.userId,
        },
        health,
        keyProbe,
        slotActive,
      };
    },
  };
}
