import { Type } from "@sinclair/typebox";

import type { CommitSessionResult, FindResultItem } from "../client.js";
import { clampScore, postProcessMemories } from "../memory-ranking.js";
import { isMemoryUri } from "../routing/memory-uri.js";

export type OpenVikingMemoryToolContext = {
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  senderId?: string;
  requesterSenderId?: string;
};

export type OpenVikingMemorySession = {
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId: string;
};

export type OpenVikingMemoryClient = {
  addSessionMessage: (
    sessionId: string,
    role: string,
    parts: Array<{ type: "text"; text: string }>,
    agentId?: string,
    createdAt?: string,
    roleId?: string,
  ) => Promise<void>;
  commitSession: (
    sessionId: string,
    options: { wait: true; agentId: string; keepRecentCount: number },
  ) => Promise<CommitSessionResult>;
  deleteUri: (uri: string, agentId?: string) => Promise<void>;
  find: (
    query: string,
    options: { targetUri: string; limit: number; scoreThreshold: number },
    agentId?: string,
  ) => Promise<{ memories?: FindResultItem[] }>;
};

export type OpenVikingMemoryToolsDeps = {
  registerTool: (toolOrFactory: unknown, opts: { name: string }) => void;
  getClient: () => Promise<OpenVikingMemoryClient>;
  normalizeSessionId: (sessionId: string) => string;
  createTempSessionId: () => string;
  extractSenderId: (ctx?: OpenVikingMemoryToolContext) => string | undefined;
  toRoleId: (senderId?: string) => string | undefined;
  resolvePluginSessionRouting: (ctx?: OpenVikingMemoryToolContext) => OpenVikingMemorySession;
  isBypassedSession: (ctx?: OpenVikingMemoryToolContext) => boolean;
  makeBypassedToolResult: (toolName: string) => unknown;
  defaultTargetUri: string;
  defaultRecallScoreThreshold: number;
  logFindRequests: boolean;
  logger: {
    info?: (message: string) => void;
    warn: (message: string) => void;
  };
};

function totalCommitMemories(r: CommitSessionResult): number {
  const m = r.memories_extracted;
  if (!m || typeof m !== "object") return 0;
  return Object.values(m).reduce((sum, n) => sum + (n ?? 0), 0);
}

export function registerOpenVikingMemoryTools(deps: OpenVikingMemoryToolsDeps): void {
  deps.registerTool(
    (ctx: OpenVikingMemoryToolContext) => ({
      name: "memory_store",
      label: "Memory Store (OpenViking)",
      description:
        "Store text in OpenViking memory pipeline by writing to a session and running memory extraction. Use when the user explicitly asks to remember, save, or store an important long-term fact, preference, project, or decision; automatic capture is threshold/commit dependent.",
      parameters: Type.Object({
        text: Type.String({ description: "Information to store as memory source text" }),
        role: Type.Optional(Type.String({ description: "Session role, default user" })),
        sessionId: Type.Optional(Type.String({ description: "Existing OpenViking session ID" })),
      }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (deps.isBypassedSession(ctx)) {
          return deps.makeBypassedToolResult("memory_store");
        }
        const session = deps.resolvePluginSessionRouting(ctx);
        const { text } = params as { text: string };
        const role =
          typeof (params as { role?: string }).role === "string"
            ? (params as { role: string }).role
            : "user";
        const explicitSessionId =
          typeof (params as { sessionId?: unknown }).sessionId === "string" &&
          (params as { sessionId: string }).sessionId.trim()
            ? deps.normalizeSessionId((params as { sessionId: string }).sessionId)
            : undefined;

        if (deps.logFindRequests) {
          deps.logger.info?.(
            `openviking: memory_store invoked (textLength=${text?.length ?? 0}, sessionId=${explicitSessionId ?? "auto"})`,
          );
        }

        let sessionId = explicitSessionId;
        let usedTempSession = false;
        try {
          const client = await deps.getClient();
          if (!sessionId) {
            sessionId = deps.createTempSessionId();
            usedTempSession = true;
          }
          const roleId = role === "user" ? deps.toRoleId(deps.extractSenderId(ctx)) : undefined;
          await client.addSessionMessage(
            sessionId,
            role,
            [{ type: "text", text }],
            session.agentId,
            undefined,
            roleId,
          );
          const commitResult = await client.commitSession(sessionId, {
            wait: true,
            agentId: session.agentId,
            keepRecentCount: 0,
          });
          const memoriesCount = totalCommitMemories(commitResult);
          if (commitResult.status === "failed") {
            deps.logger.warn(
              `openviking: memory_store commit failed (sessionId=${sessionId}): ${commitResult.error ?? "unknown"}`,
            );
            return {
              content: [{ type: "text", text: `Memory extraction failed for session ${sessionId}: ${commitResult.error ?? "unknown"}` }],
              details: {
                action: "failed",
                sessionId,
                status: "failed",
                error: commitResult.error,
                usedTempSession,
              },
            };
          }
          if (commitResult.status === "timeout") {
            deps.logger.warn(
              `openviking: memory_store commit timed out (sessionId=${sessionId}), task_id=${commitResult.task_id ?? "none"}. Memories may still be extracting in background.`,
            );
            return {
              content: [{ type: "text", text: `Memory extraction timed out for session ${sessionId}. It may still complete in the background (task_id=${commitResult.task_id ?? "none"}).` }],
              details: {
                action: "timeout",
                sessionId,
                status: "timeout",
                taskId: commitResult.task_id,
                usedTempSession,
              },
            };
          }
          if (memoriesCount === 0) {
            deps.logger.warn(
              `openviking: memory_store committed but 0 memories extracted (sessionId=${sessionId}). ` +
                "Check OpenViking server logs for embedding/extract errors (e.g. 401 API key, or extraction pipeline).",
            );
          } else {
            deps.logger.info?.(`openviking: memory_store committed, memories=${memoriesCount}`);
          }
          return {
            content: [
              {
                type: "text",
                text: `Stored in OpenViking session ${sessionId} and committed ${memoriesCount} memories.`,
              },
            ],
            details: {
              action: "stored",
              sessionId,
              memoriesCount,
              status: commitResult.status,
              archived: commitResult.archived ?? false,
              usedTempSession,
            },
          };
        } catch (err) {
          deps.logger.warn(`openviking: memory_store failed: ${String(err)}`);
          throw err;
        }
      },
    }),
    { name: "memory_store" },
  );

  deps.registerTool(
    (ctx: OpenVikingMemoryToolContext) => ({
      name: "memory_forget",
      label: "Memory Forget (OpenViking)",
      description:
        "Forget memory by URI, or search then delete when a strong single match is found.",
      parameters: Type.Object({
        uri: Type.Optional(Type.String({ description: "Exact memory URI to delete" })),
        query: Type.Optional(Type.String({ description: "Search query to find memory URI" })),
        targetUri: Type.Optional(
          Type.String({ description: "Search scope URI (default: plugin config)" }),
        ),
        limit: Type.Optional(Type.Number({ description: "Search limit (default: 5)" })),
        scoreThreshold: Type.Optional(
          Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
        ),
      }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (deps.isBypassedSession(ctx)) {
          return deps.makeBypassedToolResult("memory_forget");
        }
        const session = deps.resolvePluginSessionRouting(ctx);
        const client = await deps.getClient();
        const uri = (params as { uri?: string }).uri;
        if (uri) {
          if (!isMemoryUri(uri)) {
            return {
              content: [{ type: "text", text: `Refusing to delete non-memory URI: ${uri}` }],
              details: { action: "rejected", uri },
            };
          }
          await client.deleteUri(uri, session.agentId);
          return {
            content: [{ type: "text", text: `Forgotten: ${uri}` }],
            details: { action: "deleted", uri },
          };
        }

        const query = (params as { query?: string }).query;
        if (!query) {
          return {
            content: [{ type: "text", text: "Provide uri or query." }],
            details: { error: "missing_param" },
          };
        }

        const limit =
          typeof (params as { limit?: number }).limit === "number"
            ? Math.max(1, Math.floor((params as { limit: number }).limit))
            : 5;
        const scoreThreshold =
          typeof (params as { scoreThreshold?: number }).scoreThreshold === "number"
            ? Math.max(0, Math.min(1, (params as { scoreThreshold: number }).scoreThreshold))
            : deps.defaultRecallScoreThreshold;
        const targetUri =
          typeof (params as { targetUri?: string }).targetUri === "string"
            ? (params as { targetUri: string }).targetUri
            : deps.defaultTargetUri;
        const requestLimit = Math.max(limit * 4, 20);

        const result = await client.find(
          query,
          {
            targetUri,
            limit: requestLimit,
            scoreThreshold: 0,
          },
          session.agentId,
        );
        const candidates = postProcessMemories(result.memories ?? [], {
          limit: requestLimit,
          scoreThreshold,
          leafOnly: true,
        }).filter((item) => isMemoryUri(item.uri));
        if (candidates.length === 0) {
          return {
            content: [
              {
                type: "text",
                text: "No matching leaf memory candidates found. Try a more specific query.",
              },
            ],
            details: { action: "none", scoreThreshold },
          };
        }
        const top = candidates[0];
        if (candidates.length === 1 && clampScore(top.score) >= 0.85) {
          await client.deleteUri(top.uri, session.agentId);
          return {
            content: [{ type: "text", text: `Forgotten: ${top.uri}` }],
            details: { action: "deleted", uri: top.uri, score: top.score ?? 0 },
          };
        }

        const list = candidates
          .map((item) => `- ${item.uri} (${(clampScore(item.score) * 100).toFixed(0)}%)`)
          .join("\n");

        return {
          content: [
            {
              type: "text",
              text: `Found ${candidates.length} candidates. Specify uri:\n${list}`,
            },
          ],
          details: { action: "candidates", candidates, scoreThreshold, requestLimit },
        };
      },
    }),
    { name: "memory_forget" },
  );
}
