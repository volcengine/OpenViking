import type { OpenVikingClient } from "../client.js";
import { applyOpenVikingSessionPolicy } from "./openviking-session-policy.js";

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
  getClient: () => Promise<OpenVikingClient>;
  toOvSessionId: (sessionId?: string, sessionKey?: string) => string;
  commitIdleTimeoutSeconds: number;
  logger: {
    info: (message: string) => void;
    warn: (message: string) => void;
  };
};

const SESSION_END_TIMEOUT_MS = 1500;
const GATEWAY_STOP_TIMEOUT_MS = 4500;

async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
): Promise<T | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<null>((resolve) => {
        timer = setTimeout(() => resolve(null), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export function registerOpenVikingLifecycleHooks(deps: OpenVikingLifecycleHooksDeps): void {
  const liveSessions = new Map<string, OpenVikingHookContext>();
  const commitsInFlight = new Map<string, Promise<boolean>>();

  const rememberLiveSession = (ctx?: OpenVikingHookContext) => {
    const key = ctx?.sessionId || ctx?.sessionKey;
    if (key) liveSessions.set(key, { ...ctx });
  };

  const commitSession = async (
    ctx: OpenVikingHookContext,
    reason: "session_end" | "gateway_stop",
    timeoutMs: number,
  ): Promise<boolean> => {
    if (deps.isBypassedSession(ctx)) return true;
    const sessionRef = ctx.sessionId || ctx.sessionKey;
    if (!sessionRef) return false;
    const ovSessionId = deps.toOvSessionId(ctx.sessionId, ctx.sessionKey);
    if (!ovSessionId) return false;
    const key = sessionRef;
    let pending = commitsInFlight.get(key);
    if (!pending) {
      pending = deps.getClient()
        .then(async (client) => {
          // No agentId: the raw hook ctx.agentId is neither prefixed nor
          // sanitized, so sending it as X-OpenViking-Actor-Peer would scope
          // the commit to a peer the session's messages were never written
          // under (and openclaw ids may contain ':', which the server
          // rejects). Every other commit path here omits it too.
          const result = await client.commitSession(
            ovSessionId,
            {
              wait: false,
              timeoutMs,
              keepRecentCount: 0,
            },
          );
          return result.status !== "failed" && result.status !== "timeout";
        })
        .finally(() => {
          commitsInFlight.delete(key);
        });
      commitsInFlight.set(key, pending);
    }
    try {
      const ok = await pending;
      if (ok) {
        deps.logger.info(
          `openviking: committed OV session on ${reason} for session=${sessionRef}`,
        );
      }
      return ok;
    } catch (err) {
      deps.logger.warn(
        `openviking: failed to commit OV session on ${reason}: ${String(err)}`,
      );
      return false;
    }
  };

  deps.api.on("session_start", async (_event: unknown, ctx?: OpenVikingHookContext) => {
    deps.rememberSessionAgentId(ctx ?? {});
    if (deps.isBypassedSession(ctx)) return;
    rememberLiveSession(ctx);
    // toOvSessionId throws when both ids are absent; keep this hook fail-open.
    if (!ctx?.sessionId && !ctx?.sessionKey) return;
    if (deps.commitIdleTimeoutSeconds <= 0) return;
    const ovSessionId = deps.toOvSessionId(ctx.sessionId, ctx.sessionKey);
    if (!ovSessionId) return;
    void deps.getClient()
      .then((client) => applyOpenVikingSessionPolicy(
        client,
        ovSessionId,
        deps.commitIdleTimeoutSeconds,
      ))
      .catch((err) => {
        deps.logger.warn(`openviking: failed to apply session auto-commit policy: ${String(err)}`);
      });
  });
  deps.api.on("session_end", async (_event: unknown, ctx?: OpenVikingHookContext) => {
    deps.rememberSessionAgentId(ctx ?? {});
    const key = ctx?.sessionId || ctx?.sessionKey;
    if (!ctx || !key) return;
    rememberLiveSession(ctx);
    if (await commitSession(ctx, "session_end", SESSION_END_TIMEOUT_MS)) {
      liveSessions.delete(key);
    }
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
  deps.api.on("gateway_stop", async () => {
    const sessions = [...liveSessions.entries()];
    if (sessions.length === 0) return;
    const results = await withTimeout(
      Promise.all(sessions.map(async ([key, ctx]) => {
        const ok = await commitSession(ctx, "gateway_stop", GATEWAY_STOP_TIMEOUT_MS);
        if (ok) liveSessions.delete(key);
        return ok;
      })),
      GATEWAY_STOP_TIMEOUT_MS,
    );
    if (results === null) {
      deps.logger.warn("openviking: gateway_stop session flush timed out");
    }
  });
  deps.api.on("after_compaction", async (_event: unknown, _ctx?: OpenVikingHookContext) => {
    // Reserved hook registration for future post-compaction memory integration.
  });
}
