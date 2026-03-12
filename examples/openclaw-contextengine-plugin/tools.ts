import type { OpenVikingClient } from "./client.js";

type ToolDeps = {
  client: OpenVikingClient;
};

type CommitMemoryInput = {
  content?: string;
  memory_content?: string;
  memory_type?: string;
  priority?: number;
  category?: string;
  targetUri?: string;
  role?: string;
};

type SearchMemoriesInput = {
  query: string;
  limit?: number;
  scoreThreshold?: number;
  targetUri?: string;
};

type CommitMemoryMetadata = {
  memoryType?: string;
  priority?: number;
  category?: string;
  targetUri?: string;
};

function normalizeOptionalString(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function normalizePriority(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return undefined;
  }
  return Math.max(1, Math.min(5, Math.floor(value)));
}

function resolveCommitMemoryInput(params: CommitMemoryInput): {
  role: string;
  memoryContent: string;
  metadata: CommitMemoryMetadata;
} {
  const role = normalizeOptionalString(params.role) ?? "user";
  const memoryContent =
    normalizeOptionalString(params.memory_content) ?? normalizeOptionalString(params.content);
  if (!memoryContent) {
    throw new Error("commit_memory requires memory_content (or legacy content)");
  }

  const memoryType = normalizeOptionalString(params.memory_type);
  const category = normalizeOptionalString(params.category) ?? memoryType;
  const priority = normalizePriority(params.priority);
  const targetUri = normalizeOptionalString(params.targetUri);

  return {
    role,
    memoryContent,
    metadata: {
      memoryType,
      priority,
      category,
      targetUri,
    },
  };
}

function buildStructuredCommitContent(memoryContent: string, metadata: CommitMemoryMetadata): string {
  const lines = [
    "[openviking_memory_commit]",
    ...(metadata.memoryType ? [`memory_type: ${metadata.memoryType}`] : []),
    ...(typeof metadata.priority === "number" ? [`priority: ${metadata.priority}`] : []),
    ...(metadata.category ? [`category: ${metadata.category}`] : []),
    ...(metadata.targetUri ? [`target_uri: ${metadata.targetUri}`] : []),
    "memory_content:",
    memoryContent,
  ];
  return lines.join("\n");
}

export function createTools(deps: ToolDeps) {
  return [
    {
      name: "commit_memory",
      description: "Commit provided content to OpenViking memory extraction pipeline.",
      parameters: {
        type: "object",
        properties: {
          content: { type: "string", description: "Legacy field; use memory_content instead." },
          memory_content: { type: "string" },
          memory_type: { type: "string" },
          priority: { type: "number" },
          category: { type: "string" },
          targetUri: { type: "string" },
          role: { type: "string" },
        },
      },
      async execute(_toolCallId: string, params: CommitMemoryInput) {
        const resolved = resolveCommitMemoryInput(params);
        const commitContent = buildStructuredCommitContent(
          resolved.memoryContent,
          resolved.metadata,
        );

        const sessionId = await deps.client.createSession();
        try {
          await deps.client.addSessionMessage(sessionId, resolved.role, commitContent);
          const committed = await deps.client.commitSession(sessionId);
          return {
            content: [
              {
                type: "text" as const,
                text:
                  `Committed memory batch, extracted ${committed.extractedCount} memories.` +
                  (resolved.metadata.memoryType ? ` memory_type=${resolved.metadata.memoryType}.` : "") +
                  (resolved.metadata.category ? ` category=${resolved.metadata.category}.` : "") +
                  (typeof resolved.metadata.priority === "number"
                    ? ` priority=${resolved.metadata.priority}.`
                    : "") +
                  (resolved.metadata.targetUri ? ` target_uri=${resolved.metadata.targetUri}.` : ""),
              },
            ],
            details: {
              ...committed,
              commitMetadata: resolved.metadata,
            },
          };
        } finally {
          try {
            await deps.client.deleteSession(sessionId);
          } catch {
            // Best-effort cleanup must not mask the original tool failure.
          }
        }
      },
    },
    {
      name: "search_memories",
      description: "Search OpenViking memories by semantic query.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string" },
          limit: { type: "number" },
          scoreThreshold: { type: "number" },
          targetUri: { type: "string" },
        },
        required: ["query"],
      },
      async execute(_toolCallId: string, params: SearchMemoriesInput) {
        const result = await deps.client.find(params.query, {
          limit: params.limit,
          scoreThreshold: params.scoreThreshold,
          targetUri: params.targetUri,
        });
        return {
          content: [
            {
              type: "text",
              text: `Found ${(result.memories ?? []).length} memories.`,
            },
          ],
          details: result,
        };
      },
    },
  ];
}
