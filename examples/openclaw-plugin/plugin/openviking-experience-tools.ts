import { Type } from "@sinclair/typebox";

import type { FindResult } from "../client.js";

const EXPERIENCE_TARGET_URI = "viking://user/memories/experiences/";
const DEFAULT_LIMIT = 5;
const MAX_LIMIT = 20;
const EXPERIENCE_SIDECAR_FILENAMES = new Set([".abstract.md", ".overview.md", ".relations.json"]);

export type OpenVikingExperienceToolContext = {
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  senderId?: string;
  requesterSenderId?: string;
};

export type OpenVikingExperienceSession = {
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId: string;
  actorPeerId?: string;
};

type OpenVikingExperienceClient = {
  find: (
    query: string,
    options: { targetUri: string; limit: number; scoreThreshold?: number },
    actorPeerId?: string,
  ) => Promise<FindResult>;
  read: (uri: string, actorPeerId?: string) => Promise<unknown>;
};

export type OpenVikingExperienceToolsDeps = {
  registerTool: (toolOrFactory: unknown, opts: { name: string }) => void;
  getClient: () => Promise<OpenVikingExperienceClient>;
  resolvePluginSessionRouting: (ctx?: OpenVikingExperienceToolContext) => OpenVikingExperienceSession;
  isBypassedSession: (ctx?: OpenVikingExperienceToolContext) => boolean;
  makeBypassedToolResult: (toolName: string) => unknown;
  userId?: string;
};

function clampLimit(value: unknown): number {
  const parsed = Number.isFinite(Number(value)) ? Math.trunc(Number(value)) : DEFAULT_LIMIT;
  return Math.max(1, Math.min(MAX_LIMIT, parsed));
}

function experienceRelativePath(uri: string): string {
  return uri.match(/^viking:\/\/user\/[^/]+\/memories\/experiences\/(.+)$/)?.[1] ?? "";
}

export function isCanonicalExperienceUri(uri: unknown, expectedUserId?: string): uri is string {
  const value = typeof uri === "string" ? uri.trim() : "";
  if (!value || value.includes("?") || value.includes("#")) {
    return false;
  }
  const owner = value.match(/^viking:\/\/user\/([^/]+)\//)?.[1] ?? "";
  if (expectedUserId?.trim() && owner !== expectedUserId.trim()) {
    return false;
  }
  const relative = experienceRelativePath(value);
  const segments = relative.split("/");
  const basename = segments.at(-1) ?? "";
  return Boolean(
    relative
    && segments.every((segment) => segment && segment !== "." && segment !== "..")
    && !EXPERIENCE_SIDECAR_FILENAMES.has(basename)
  );
}

function titleFromUri(uri: string): string {
  const basename = uri.split("/").at(-1) ?? uri;
  const title = basename.replace(/\.md$/i, "");
  try {
    return decodeURIComponent(title);
  } catch {
    return title;
  }
}

function jsonToolResult(payload: Record<string, unknown>): {
  content: Array<{ type: "text"; text: string }>;
} {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
  };
}

export function registerOpenVikingExperienceTools(deps: OpenVikingExperienceToolsDeps): void {
  deps.registerTool(
    (ctx: OpenVikingExperienceToolContext) => ({
      name: "search_experience",
      label: "Search Experience (OpenViking)",
      description:
        "Search reusable task-execution experiences for the current OpenViking user. " +
        "Use before executing operational or multi-step tasks, then call read_experience for selected results.",
      parameters: Type.Object({
        query: Type.String({ description: "Current task, situation, or execution problem to search for" }),
        limit: Type.Optional(Type.Integer({
          description: `Maximum results to return. Default: ${DEFAULT_LIMIT}; maximum: ${MAX_LIMIT}`,
          minimum: 1,
          maximum: MAX_LIMIT,
        })),
      }, { additionalProperties: false }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (deps.isBypassedSession(ctx)) {
          return deps.makeBypassedToolResult("search_experience");
        }
        const query = typeof params.query === "string" ? params.query.trim() : "";
        if (!query) {
          throw new Error("query is required");
        }
        const session = deps.resolvePluginSessionRouting(ctx);
        const client = await deps.getClient();
        const result = await client.find(query, {
          targetUri: EXPERIENCE_TARGET_URI,
          limit: clampLimit(params.limit),
        }, session.actorPeerId);
        const results = (result.memories ?? [])
          .filter((item) => isCanonicalExperienceUri(item.uri, deps.userId))
          .map((item) => ({
            uri: item.uri,
            title: titleFromUri(item.uri),
            score: typeof item.score === "number" ? item.score : 0,
            snippet: item.abstract || item.overview || "",
          }));
        return jsonToolResult({ results });
      },
    }),
    { name: "search_experience" },
  );

  deps.registerTool(
    (ctx: OpenVikingExperienceToolContext) => ({
      name: "read_experience",
      label: "Read Experience (OpenViking)",
      description:
        "Read the complete Markdown body of one Experience returned by search_experience and use it as execution guidance.",
      parameters: Type.Object({
        uri: Type.String({ description: "Exact canonical Experience URI returned by search_experience" }),
      }, { additionalProperties: false }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (deps.isBypassedSession(ctx)) {
          return deps.makeBypassedToolResult("read_experience");
        }
        const uri = typeof params.uri === "string" ? params.uri.trim() : "";
        if (!isCanonicalExperienceUri(uri, deps.userId)) {
          throw new Error("uri must be a canonical OpenViking Experience URI returned by search_experience");
        }
        const session = deps.resolvePluginSessionRouting(ctx);
        const client = await deps.getClient();
        const content = await client.read(uri, session.actorPeerId);
        const text = typeof content === "string" ? content : JSON.stringify(content, null, 2);
        return jsonToolResult({ uri, content: text });
      },
    }),
    { name: "read_experience" },
  );
}
