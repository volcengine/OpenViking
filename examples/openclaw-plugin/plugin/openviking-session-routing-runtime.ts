import type { QueryConfigContext } from "../query-config.js";
import {
  createSessionAgentResolver,
  openClawSessionToOvStorageId,
} from "../routing/identity-routing.js";

type Logger = {
  info: (message: string) => void;
};

export type SessionAgentLookup = {
  agentId?: string;
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
};

export type PluginSessionRouting = {
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId: string;
};

export function createOpenVikingSessionRoutingRuntime(options: {
  peerPrefix: string;
  logFindRequests: boolean;
  logger: Logger;
}) {
  const sessionAgentResolver = createSessionAgentResolver(options.peerPrefix);

  const rememberSessionAgentId = (ctx: SessionAgentLookup) => {
    sessionAgentResolver.remember(ctx);
  };

  const resolveAgentId = (
    sessionId?: string,
    sessionKey?: string,
    ovSessionId?: string,
  ): string => {
    const sid = typeof sessionId === "string" ? sessionId.trim() : "";
    const sk = typeof sessionKey === "string" ? sessionKey.trim() : "";
    const ovSid = typeof ovSessionId === "string" ? ovSessionId.trim() : "";
    const result = sessionAgentResolver.resolve(sid, sk, ovSid);
    if (options.logFindRequests) {
      options.logger.info(
        `openviking: resolveAgentId ${JSON.stringify({
          sessionId: sid || "(empty)",
          sessionKey: sk || "(empty)",
          ovSessionId: ovSid || "(empty)",
          parsedConfigPeerPrefix: options.peerPrefix,
          mappedResolvedAgentId: result.mappedResolvedAgentId,
          resolvedBeforeSanitize: result.resolvedBeforeSanitize,
          resolved: result.resolved,
          branch: result.branch,
          aliases: result.aliases,
          fromExplicitBinding: result.fromExplicitBinding,
        })}`,
      );
    }
    return result.resolved;
  };

  const resolvePluginSessionRouting = (ctx?: SessionAgentLookup): PluginSessionRouting => {
    const sessionId = typeof ctx?.sessionId === "string" ? ctx.sessionId.trim() : "";
    const sessionKey = typeof ctx?.sessionKey === "string" ? ctx.sessionKey.trim() : "";
    let ovSessionId = typeof ctx?.ovSessionId === "string" ? ctx.ovSessionId.trim() : "";

    if (!ovSessionId && (sessionId || sessionKey)) {
      ovSessionId = openClawSessionToOvStorageId(
        sessionId || undefined,
        sessionKey || undefined,
      );
    }

    const session = {
      agentId: ctx?.agentId,
      sessionId: sessionId || undefined,
      sessionKey: sessionKey || undefined,
      ovSessionId: ovSessionId || undefined,
    };
    rememberSessionAgentId(session);

    return {
      sessionId: session.sessionId,
      sessionKey: session.sessionKey,
      ovSessionId: session.ovSessionId,
      agentId: resolveAgentId(session.sessionId, session.sessionKey, session.ovSessionId),
    };
  };

  const toQueryConfigContext = (session: PluginSessionRouting): QueryConfigContext => ({
    peerId: session.agentId,
    sessionId: session.sessionId,
    sessionKey: session.sessionKey,
    ovSessionId: session.ovSessionId,
  });

  return {
    rememberSessionAgentId,
    resolveAgentId,
    resolvePluginSessionRouting,
    toQueryConfigContext,
  };
}
