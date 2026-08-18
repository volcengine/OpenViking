import { defineTool } from "@deepseek-ai/dsh-tools";

export function registerOpenVikingTools(ctx, client, runtime) {
  ctx.tools.register(textTool({
    name: "viking_search",
    description:
      "Search OpenViking memories, resources, and skills. Use this for prior decisions, preferences, and project knowledge not present in the current context.",
    parameters: {
      query: { type: "string", required: true, description: "Semantic search query." },
      scope: { type: "string", description: "Optional viking:// URI prefix." },
      limit: { type: "integer", description: "Maximum results, from 1 to 50." },
    },
    async execute(args, exec) {
      const actorPeerId = await peerFor(runtime, exec);
      const results = await client.find(args.query, {
        targetUri: args.scope,
        limit: Math.max(1, Math.min(50, args.limit || 10)),
        actorPeerId,
      });
      if (results.length === 0) return "No results found.";
      return results.map(result => {
        const abstract = result.abstract.length > client.config.recallMaxContentChars
          ? `${result.abstract.slice(0, client.config.recallMaxContentChars)}...`
          : result.abstract;
        return `[${result.score.toFixed(2)}] ${result.uri}\n  ${abstract}`;
      }).join("\n\n");
    },
  }));

  ctx.tools.register(textTool({
    name: "viking_read",
    description:
      "Read OpenViking content by viking:// URI at abstract, overview, or full detail.",
    parameters: {
      uri: { type: "string", required: true, description: "The viking:// URI to read." },
      level: {
        type: "string",
        required: true,
        enum: ["abstract", "overview", "full"],
        description: "Detail level.",
      },
    },
    async execute(args, exec) {
      const content = await client.read(
        args.uri,
        args.level,
        await peerFor(runtime, exec),
      );
      return typeof content === "string" && content
        ? content
        : `No content at ${args.uri}`;
    },
  }));

  ctx.tools.register(textTool({
    name: "viking_browse",
    description: "List an OpenViking directory or inspect metadata for a viking:// URI.",
    parameters: {
      action: {
        type: "string",
        required: true,
        enum: ["list", "stat"],
        description: "Browse operation.",
      },
      uri: { type: "string", description: "URI to browse. Defaults to viking://." },
    },
    async execute(args, exec) {
      const actorPeerId = await peerFor(runtime, exec);
      const uri = args.uri || "viking://";
      if (args.action === "stat") {
        const info = await client.stat(uri, actorPeerId);
        return info ? JSON.stringify(info, null, 2) : `Not found: ${uri}`;
      }
      const entries = await client.list(uri, actorPeerId);
      if (entries.length === 0) return `Empty directory: ${uri}`;
      return entries
        .map(entry => `${entry.isDir ? "[dir]" : "[file]"} ${entry.name || entry.uri}`)
        .join("\n");
    },
  }));

  ctx.tools.register(textTool({
    name: "viking_remember",
    description:
      "Store an important fact, preference, decision, or gotcha in the current OpenViking session.",
    parameters: {
      content: { type: "string", required: true, description: "Fact to remember." },
      category: {
        type: "string",
        description: "Optional category such as preference, decision, entity, event, or pattern.",
      },
    },
    async execute(args, exec) {
      if (!exec.agent) throw new Error("viking_remember requires a calling agent");
      const state = await runtime.initialize(exec.agent);
      if (!state.ready) return "OpenViking server is not reachable.";
      const category = args.category || "general";
      const payload = {
        role: "user",
        content: `[Remember — ${category}] ${args.content}`,
        ...(state.config.peerId ? { peer_id: state.config.peerId } : {}),
      };
      const response = await client.addMessage(
        state.ovSessionId,
        payload,
        state.config.peerId,
      );
      return response.ok
        ? `Remembered in OpenViking: "${args.content}" (${category})`
        : `Failed to remember: ${response.error?.message || response.error?.code || "unknown error"}`;
    },
  }));

  ctx.tools.register(textTool({
    name: "viking_forget",
    description:
      "Permanently delete an OpenViking item only after the user explicitly asks to forget or delete it. Use an exact URI, or delete the strongest search match above score 0.8.",
    parameters: {
      uri: { type: "string", description: "Exact viking:// URI to delete." },
      query: { type: "string", description: "Search query used when URI is omitted." },
    },
    async execute(args, exec) {
      const actorPeerId = await peerFor(runtime, exec);
      let uri = args.uri;
      if (!uri && args.query) {
        const results = await client.find(args.query, { limit: 1, actorPeerId });
        if (!results[0] || results[0].score <= 0.8) {
          return "No strong match found (score > 0.8 required).";
        }
        uri = results[0].uri;
      }
      if (!uri) return "Provide either uri or query.";
      return await client.forget(uri, false, actorPeerId)
        ? `Deleted: ${uri}`
        : `Failed to delete: ${uri}`;
    },
  }));

  ctx.tools.register(textTool({
    name: "viking_add_resource",
    description: "Ingest an HTTP(S) or git URL into OpenViking for indexed retrieval.",
    parameters: {
      url: { type: "string", required: true, description: "Remote URL to ingest." },
      reason: { type: "string", description: "Why this resource is relevant." },
    },
    async execute(args, exec) {
      const result = await client.addResource(
        args.url,
        args.reason,
        await peerFor(runtime, exec),
      );
      return result?.root_uri
        ? `Ingested: ${result.root_uri}`
        : `Failed to ingest: ${args.url}`;
    },
  }));

  ctx.tools.register(textTool({
    name: "viking_archive_expand",
    description: "Read original messages from one archive in the current DSH session.",
    parameters: {
      archive_id: { type: "string", required: true, description: "Archive id such as archive_001." },
    },
    async execute(args, exec) {
      if (!exec.agent) throw new Error("viking_archive_expand requires a calling agent");
      const state = await runtime.initialize(exec.agent);
      const archive = await client.getSessionArchive(
        state.ovSessionId,
        args.archive_id,
        state.config.peerId,
      );
      if (!archive) return `Archive not found: ${args.archive_id}`;
      const messages = Array.isArray(archive.messages) ? archive.messages : [];
      const header = [
        `## ${archive.archive_id || args.archive_id}`,
        archive.abstract ? `**Summary**: ${archive.abstract}` : "",
        `**Messages**: ${messages.length}`,
      ].filter(Boolean).join("\n");
      const body = messages.map(formatArchiveMessage).join("\n\n");
      return body ? `${header}\n\n${body}` : header;
    },
  }));
}

/** Presentation identity per tool: pending-card kind and title verb. */
const TOOL_PRESENTATION = {
  viking_search: { kind: "read", title: args => `OpenViking search: ${args.query}` },
  viking_read: { kind: "read", title: args => `OpenViking read: ${args.uri}` },
  viking_browse: { kind: "read", title: args => `OpenViking browse: ${args.uri ?? "viking://"}` },
  viking_remember: { kind: "other", title: () => "OpenViking remember" },
  viking_forget: { kind: "other", title: args => `OpenViking forget: ${args.uri ?? args.query ?? ""}` },
  viking_add_resource: { kind: "other", title: args => `OpenViking ingest: ${args.url}` },
  viking_archive_expand: { kind: "read", title: () => "OpenViking archive expand" },
};

/**
 * All seven tools return model-facing text; `defineTool` owns parameter and
 * output JSON-schema conversion, validation wiring, and reserved-name checks,
 * so the definitions here stay declarative and track dsh's ToolDefinition
 * contract through the pinned peer instead of a hand-built structure.
 */
function textTool(definition) {
  const presentation = TOOL_PRESENTATION[definition.name];
  return defineTool({
    ...definition,
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }],
    },
    presentCall: args => ({
      card: "generic",
      kind: presentation.kind,
      title: presentation.title(args),
      rawInput: args,
    }),
  });
}

async function peerFor(runtime, exec) {
  if (!exec.agent) return undefined;
  return (await runtime.initialize(exec.agent)).config.peerId;
}

function formatArchiveMessage(message) {
  const role = String(message?.role || "unknown");
  const parts = Array.isArray(message?.parts) ? message.parts : [];
  const body = parts.map(formatArchivePart).filter(Boolean).join("\n");
  return `[${role}]: ${body || "(empty)"}`;
}

function formatArchivePart(part) {
  if (!part || typeof part !== "object") return "";
  if (part.type === "text") return String(part.text || "");
  if (part.type === "context") {
    return `[Context: ${part.uri || "unknown"}]\n${part.abstract || ""}`.trim();
  }
  if (part.type === "tool") {
    const lines = [`[Tool: ${part.tool_name || "unknown"}]`];
    if (part.tool_input !== undefined) {
      lines.push(`Input: ${JSON.stringify(part.tool_input)}`);
    }
    if (part.tool_output) lines.push(`Output: ${part.tool_output}`);
    return lines.join("\n");
  }
  return `[${part.type || "unknown"}]: ${JSON.stringify(part)}`;
}
