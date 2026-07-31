export type OpenVikingHookContext = {
  agentId?: string;
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
};

export type ContextEngineCommitPort = {
  commitOVSession: (ctx: { sessionId: string; sessionKey?: string }) => Promise<boolean>;
};

export type OpenVikingLifecycleHookApi = {
  on: (
    hookName: string,
    handler: (event: unknown, ctx?: OpenVikingHookContext) => unknown,
    opts?: { priority?: number },
  ) => void;
};

export type OpenVikingLifecycleHooksDeps = {
  api: OpenVikingLifecycleHookApi;
  rememberSessionAgentId: (ctx: OpenVikingHookContext) => void;
  isBypassedSession: (ctx?: OpenVikingHookContext) => boolean;
  verboseRoutingInfo: (message: string) => void;
  getContextEngine: () => ContextEngineCommitPort | null;
  logger: {
    info: (message: string) => void;
    warn: (message: string) => void;
  };
};

export function registerOpenVikingLifecycleHooks(deps: OpenVikingLifecycleHooksDeps): void {
  deps.api.on("session_start", async (_event: unknown, ctx?: OpenVikingHookContext) => {
    deps.rememberSessionAgentId(ctx ?? {});
  });
  deps.api.on("session_end", async (_event: unknown, ctx?: OpenVikingHookContext) => {
    deps.rememberSessionAgentId(ctx ?? {});
  });
  deps.api.on("before_reset", async (_event: unknown, ctx?: OpenVikingHookContext) => {
    if (deps.isBypassedSession(ctx)) {
      deps.verboseRoutingInfo(
        `openviking: bypassing before_reset due to session pattern match (sessionKey=${ctx?.sessionKey ?? "none"}, sessionId=${ctx?.sessionId ?? "none"})`,
      );
      return;
    }
    const sessionId = ctx?.sessionId;
    const contextEngine = deps.getContextEngine();
    if (sessionId && contextEngine) {
      try {
        const ok = await contextEngine.commitOVSession({
          sessionId,
          sessionKey: ctx?.sessionKey,
        });
        if (ok) {
          deps.logger.info(`openviking: committed OV session on reset for session=${sessionId}`);
        }
      } catch (err) {
        deps.logger.warn(`openviking: failed to commit OV session on reset: ${String(err)}`);
      }
    }
  });
  deps.api.on("after_compaction", async (_event: unknown, _ctx?: OpenVikingHookContext) => {
    // Reserved hook registration for future post-compaction memory integration.
  });
}
