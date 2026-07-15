import { createHash } from "node:crypto";

const DEFAULT_OPENCLAW_AGENT_ID = "main";

/** OpenClaw session UUID (path-safe on Windows). */
const OPENVIKING_OV_SESSION_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const WINDOWS_BAD_SESSION_SEGMENT = /[:<>"\\/|?\u0000-\u001f]/;

export type SessionAgentLookup = {
  agentId?: string;
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
};

export type OpenVikingPeerRole = "none" | "assistant" | "person";

export function sanitizeOpenVikingPeerId(raw?: string): string | undefined {
  const normalized = raw
    ?.trim()
    .replace(/[^a-zA-Z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
  return normalized || undefined;
}

export function resolveOpenVikingMessagePeerId(params: {
  peerRole: OpenVikingPeerRole;
  role?: string;
  personPeerId?: string;
  assistantPeerId?: string;
}): string | undefined {
  if (params.peerRole === "person" && params.role === "user") {
    return params.personPeerId;
  }
  if (params.peerRole === "assistant" && params.role === "assistant") {
    return params.assistantPeerId;
  }
  return undefined;
}

export function resolveOpenVikingActorPeerId(params: {
  peerRole: OpenVikingPeerRole;
  personPeerId?: string;
  assistantPeerId?: string;
}): string | undefined {
  const actorPeerId = resolveOpenVikingMessagePeerId({
    ...params,
    role: params.peerRole === "person" ? "user" : "assistant",
  });
  if (params.peerRole === "person" && !actorPeerId) {
    throw new Error("openviking: peer_role=person requires a sender identity");
  }
  return actorPeerId;
}

export type SessionAgentResolveBranch =
  | "session_resolved"
  | "config_only_fallback"
  | "default_no_session";

export type SessionAgentResolveResult = {
  resolved: string;
  resolvedBeforeSanitize: string;
  branch: SessionAgentResolveBranch;
  mappedResolvedAgentId: string | null;
  aliases: string[];
  fromExplicitBinding: boolean;
};

/**
 * Map OpenClaw session identity to an OpenViking session_id that is safe as a single
 * AGFS path segment on Windows (no `:` etc.). Prefer UUID sessionId when present;
 * otherwise derive a stable sha256 from sessionKey.
 */
export function openClawSessionToOvStorageId(
  sessionId: string | undefined,
  sessionKey: string | undefined,
): string {
  const sid = typeof sessionId === "string" ? sessionId.trim() : "";
  const key = typeof sessionKey === "string" ? sessionKey.trim() : "";

  if (sid && OPENVIKING_OV_SESSION_UUID.test(sid)) {
    return sid.toLowerCase();
  }
  if (key) {
    return createHash("sha256").update(key, "utf8").digest("hex");
  }
  if (sid) {
    if (WINDOWS_BAD_SESSION_SEGMENT.test(sid)) {
      return createHash("sha256").update(`openclaw-session:${sid}`, "utf8").digest("hex");
    }
    return sid;
  }
  throw new Error("openviking: need sessionId or sessionKey for OV session path");
}

/** Normalize a hook/tool session ref (uuid, sessionKey, or already-safe id) for OV storage. */
export function openClawSessionRefToOvStorageId(ref: string): string {
  const t = ref.trim();
  if (!t) {
    throw new Error("openviking: empty session ref");
  }
  if (OPENVIKING_OV_SESSION_UUID.test(t)) {
    return t.toLowerCase();
  }
  if (WINDOWS_BAD_SESSION_SEGMENT.test(t)) {
    return createHash("sha256").update(t, "utf8").digest("hex");
  }
  return t;
}

/**
 * OpenViking peer identifiers allow only [a-zA-Z0-9_-].
 * OpenClaw ids may contain ":"; never send raw colons in X-OpenViking-Actor-Peer.
 */
export function sanitizeOpenVikingAgentIdHeader(raw: string): string {
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
      aliases.add(
        openClawSessionToOvStorageId(
          sid || undefined,
          sk || undefined,
        ),
      );
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
    const resolved = sanitizeOpenVikingAgentIdHeader(resolvedBeforeSanitize);
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
      resolved = sanitizeOpenVikingAgentIdHeader(resolvedBeforeSanitize);
      branch = "session_resolved";
    } else if (!prefix) {
      resolvedBeforeSanitize = DEFAULT_OPENCLAW_AGENT_ID;
      resolved = DEFAULT_OPENCLAW_AGENT_ID;
      branch = "default_no_session";
    } else {
      resolvedBeforeSanitize = `${prefix}_${DEFAULT_OPENCLAW_AGENT_ID}`;
      resolved = sanitizeOpenVikingAgentIdHeader(resolvedBeforeSanitize);
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
