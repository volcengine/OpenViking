export type OpenVikingHookContext = {
  agentId?: string;
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
};

export type ContextEngineCommitPort = {
  commitOVSession: (
    ctx: { sessionId: string; sessionKey?: string },
    options?: { wait: boolean; keepRecentCount: number },
  ) => Promise<boolean>;
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
  toOVSessionId: (sessionId: string, sessionKey?: string) => string;
  isBypassedSession: (ctx?: OpenVikingHookContext) => boolean;
  verboseRoutingInfo: (message: string) => void;
  getContextEngine: () => ContextEngineCommitPort | null;
  logger: {
    info: (message: string) => void;
    warn: (message: string) => void;
  };
};

export function registerOpenVikingLifecycleHooks(deps: OpenVikingLifecycleHooksDeps): void {
  const inFlightFinalCommits = new Map<string, Promise<boolean>>();

  const finalizeSession = async (
    ctx: OpenVikingHookContext | undefined,
    trigger: "session_end" | "before_reset",
  ): Promise<boolean> => {
    if (deps.isBypassedSession(ctx)) {
      deps.verboseRoutingInfo(
        `openviking: bypassing ${trigger} due to session pattern match (sessionKey=${ctx?.sessionKey ?? "none"}, sessionId=${ctx?.sessionId ?? "none"})`,
      );
      return false;
    }

    const sessionId = ctx?.sessionId;
    const contextEngine = deps.getContextEngine();
    if (!sessionId || !contextEngine) {
      return false;
    }

    const ovSessionId = deps.toOVSessionId(sessionId, ctx?.sessionKey);
    const existingCommit = inFlightFinalCommits.get(ovSessionId);
    if (existingCommit) {
      deps.verboseRoutingInfo(
        `openviking: reusing in-flight final commit for session=${sessionId} trigger=${trigger}`,
      );
      const ok = await existingCommit;
      if (ok) {
        return true;
      }
      deps.verboseRoutingInfo(
        `openviking: retrying failed shared final commit for session=${sessionId} trigger=${trigger}`,
      );
      return finalizeSession(ctx, trigger);
    }

    const commitContext = { sessionId, sessionKey: ctx?.sessionKey };
    let handledCommit: Promise<boolean>;
    handledCommit = Promise.resolve()
      .then(() => contextEngine.commitOVSession(
        commitContext,
        { wait: false, keepRecentCount: 0 },
      ))
      .then((ok) => {
        if (ok) {
          deps.logger.info(`openviking: committed OV session on ${trigger} for session=${sessionId}`);
        }
        return ok;
      })
      .catch((err) => {
        deps.logger.warn(`openviking: failed to commit OV session on ${trigger}: ${String(err)}`);
        return false;
      })
      .finally(() => {
        if (inFlightFinalCommits.get(ovSessionId) === handledCommit) {
          inFlightFinalCommits.delete(ovSessionId);
        }
      });
    inFlightFinalCommits.set(ovSessionId, handledCommit);
    return handledCommit;
  };

  deps.api.on("session_start", async (_event: unknown, ctx?: OpenVikingHookContext) => {
    deps.rememberSessionAgentId(ctx ?? {});
  });
  deps.api.on("session_end", async (_event: unknown, ctx?: OpenVikingHookContext) => {
    deps.rememberSessionAgentId(ctx ?? {});
    await finalizeSession(ctx, "session_end");
  });
  deps.api.on("before_reset", async (_event: unknown, ctx?: OpenVikingHookContext) => {
    await finalizeSession(ctx, "before_reset");
  });
  deps.api.on("after_compaction", async (_event: unknown, _ctx?: OpenVikingHookContext) => {
    // Reserved hook registration for future post-compaction memory integration.
  });
}
