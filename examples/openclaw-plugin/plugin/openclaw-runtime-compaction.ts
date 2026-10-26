// Bridge to OpenClaw's built-in compactor for sessions that bypass OpenViking.
// The host exports `delegateCompactionToRuntime` from `openclaw/plugin-sdk/core`
// (present since the earliest supported host); it is resolved lazily because the
// plugin builds without the host SDK installed.

export type RuntimeCompactionResult = {
  ok: boolean;
  compacted: boolean;
  reason?: string;
  result?: {
    summary?: string;
    firstKeptEntryId?: string;
    tokensBefore: number;
    tokensAfter?: number;
    details?: unknown;
  };
};

export type RuntimeCompactionDelegate = (
  params: Record<string, unknown>,
) => Promise<RuntimeCompactionResult>;

const PLUGIN_SDK_CORE_SPECIFIER = "openclaw/plugin-sdk/core";

let cached: Promise<RuntimeCompactionDelegate | undefined> | undefined;

export function loadRuntimeCompactionDelegate(): Promise<RuntimeCompactionDelegate | undefined> {
  cached ??= (async () => {
    try {
      const mod = (await import(PLUGIN_SDK_CORE_SPECIFIER)) as {
        delegateCompactionToRuntime?: unknown;
      };
      return typeof mod?.delegateCompactionToRuntime === "function"
        ? (mod.delegateCompactionToRuntime as RuntimeCompactionDelegate)
        : undefined;
    } catch {
      return undefined;
    }
  })();
  return cached;
}
