export const HARNESS_KEYS: Record<string, string>;

export function harnessKey(harness: string): string;

export function loadPluginSettings(
  harness: string,
  env?: Record<string, string | undefined>,
  options?: { cwd?: string; clientVersion?: string; cliFile?: Record<string, any> | null },
): Record<string, any>;

export function resolveSettings(
  harness: string,
  options?: {
    env?: Record<string, string | undefined>;
    cwd?: string;
    legacy?: Record<string, any>;
    clientVersion?: string;
    cliFile?: Record<string, any> | null;
  },
): {
  settings: Record<string, any>;
  configured: Set<string>;
  sources: Record<string, string>;
  plugin: Record<string, any>;
};

export function buildPluginConfig(
  harness: string,
  options?: {
    cwd?: string;
    env?: Record<string, string | undefined>;
    legacy?: Record<string, any> | null;
    manifestUrl?: string | URL;
    version?: string;
    hostInput?: {
      peerId?: string;
      account?: string;
      user?: string;
      apiKey?: string;
      baseUrl?: string;
    };
    logFile?: string;
    rootKeyFallback?: boolean;
    deriveEffectivePeer?: boolean;
  },
): Record<string, any>;

export function resolveWorkspaceSettings(
  cwd: string,
  env?: Record<string, string | undefined>,
  options?: { clientVersion?: string },
): {
  settings: Record<string, any>;
  root: string;
  provenance: Record<string, any>;
  warnings: string[];
  announced: string[];
  value?: Record<string, any>;
};

export function normalizeRewriteMode(value: unknown, fallback?: string): string;
