import {
  compileSessionPatterns,
  shouldBypassSession,
} from "../text-utils.js";

type BypassRuntimeConfig = {
  bypassSessionPatterns: string[];
};

type SessionBypassContext = {
  sessionId?: string;
  sessionKey?: string;
};

export function createOpenVikingBypassRuntime<TConfig extends BypassRuntimeConfig>(options: {
  cfg: TConfig;
}) {
  const bypassSessionPatterns = compileSessionPatterns(options.cfg.bypassSessionPatterns);

  const isBypassedSession = (ctx?: SessionBypassContext): boolean =>
    shouldBypassSession(ctx ?? {}, bypassSessionPatterns);

  return {
    isBypassedSession,
  };
}
