import { openClawSessionToOvStorageId } from "./context-engine.js";

const DEFAULT_OPENCLAW_AGENT_ID = "main";

export type SessionAgentLookup = { agentId?: string; sessionId?: string; sessionKey?: string; ovSessionId?: string };

type SessionAgentResolveBranch =
  | "session_resolved"
  | "config_only_fallback"
  | "default_no_session";

export type SessionAgentResolveResult = {
  resolved: string; resolvedBeforeSanitize: string; branch: SessionAgentResolveBranch;
  mappedResolvedAgentId: string | null; aliases: string[]; fromExplicitBinding: boolean;
};

/**
 * OpenClaw ids may contain ":" (e.g. session keys), while OpenViking
 * peer/session metadata is path-friendly.
 */
export function sanitizeRuntimeAgentId(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) {
    return "default";
  }
  const normalized = trimmed
    .replace(/[^a-zA-Z0-9_-]/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "");
  return normalized.length > 0 ? normalized : "ov_agent";
}

function extractAgentIdFromSessionKey(sessionKey?: string): string | undefined {
  const raw = typeof sessionKey === "string" ? sessionKey.trim() : "";
  if (!raw) {
    return undefined;
  }

  const match = raw.match(/^agent:([^:]+):/);
  const agentId = match?.[1]?.trim();
  return agentId || undefined;
}

function collectSessionAgentAliases(
  sessionId?: string,
  sessionKey?: string,
  ovSessionId?: string,
): string[] {
  const aliases = new Set<string>();
  const sid = typeof sessionId === "string" ? sessionId.trim() : "";
  const sk = typeof sessionKey === "string" ? sessionKey.trim() : "";
  const ovSid = typeof ovSessionId === "string" ? ovSessionId.trim() : "";

  if (sid) {
    aliases.add(sid);
  }
  if (sk) {
    aliases.add(sk);
  }
  if (ovSid) {
    aliases.add(ovSid);
  }

  if (!ovSid && (sid || sk)) {
    try {
      aliases.add(openClawSessionToOvStorageId(sid || undefined, sk || undefined));
    } catch {
      /* need a resolvable OpenClaw session identity */
    }
  }

  return [...aliases];
}

export function createSessionAgentResolver(configAgentId: string) {
  const configAgentPrefix = configAgentId.trim() === "default" ? "" : configAgentId.trim();
  const sessionAgentIds = new Map<string, string>();

  const remember = (ctx: SessionAgentLookup): void => {
    const sessionScopedAgentId =
      extractAgentIdFromSessionKey(ctx.sessionKey) ||
      extractAgentIdFromSessionKey(ctx.sessionId);
    const rawAgentId =
      (typeof ctx.agentId === "string" ? ctx.agentId.trim() : "") ||
      sessionScopedAgentId ||
      "";
    if (!rawAgentId) {
      return;
    }

    const prefix = configAgentPrefix;
    const resolvedBeforeSanitize = prefix ? `${prefix}_${rawAgentId}` : rawAgentId;
    const resolved = sanitizeRuntimeAgentId(resolvedBeforeSanitize);
    for (const alias of collectSessionAgentAliases(ctx.sessionId, ctx.sessionKey, ctx.ovSessionId)) {
      sessionAgentIds.set(alias, resolved);
    }
  };

  const resolve = (
    sessionId?: string,
    sessionKey?: string,
    ovSessionId?: string,
  ): SessionAgentResolveResult => {
    const aliases = collectSessionAgentAliases(sessionId, sessionKey, ovSessionId);
    const mappedAlias = aliases.find((alias) => sessionAgentIds.has(alias));
    const mappedResolvedAgentId = mappedAlias ? sessionAgentIds.get(mappedAlias) : undefined;
    const sessionScopedAgentId =
      extractAgentIdFromSessionKey(sessionKey) ||
      extractAgentIdFromSessionKey(sessionId);

    let resolvedBeforeSanitize: string;
    let resolved: string;
    let branch: SessionAgentResolveBranch;
    const prefix = configAgentPrefix;

    if (mappedResolvedAgentId) {
      resolvedBeforeSanitize = mappedResolvedAgentId;
      resolved = mappedResolvedAgentId;
      branch = "session_resolved";
    } else if (sessionScopedAgentId) {
      resolvedBeforeSanitize = prefix ? `${prefix}_${sessionScopedAgentId}` : sessionScopedAgentId;
      resolved = sanitizeRuntimeAgentId(resolvedBeforeSanitize);
      branch = "session_resolved";
    } else if (!prefix) {
      resolvedBeforeSanitize = DEFAULT_OPENCLAW_AGENT_ID;
      resolved = DEFAULT_OPENCLAW_AGENT_ID;
      branch = "default_no_session";
    } else {
      resolvedBeforeSanitize = `${prefix}_${DEFAULT_OPENCLAW_AGENT_ID}`;
      resolved = sanitizeRuntimeAgentId(resolvedBeforeSanitize);
      branch = "config_only_fallback";
    }

    return {
      resolved,
      resolvedBeforeSanitize,
      branch,
      mappedResolvedAgentId: mappedResolvedAgentId ?? null,
      aliases,
      fromExplicitBinding: !!(mappedResolvedAgentId || sessionScopedAgentId),
    };
  };

  return {
    remember,
    resolve,
  };
}
