import { boundContextSummary, createUserMessage } from "@deepseek-ai/dsh-llm";

import { createRecallCompressor } from "./compressor.mjs";
import { MCP_SERVER_NAME } from "./config.mjs";
import { decode, OPENVIKING_PLUGIN_KIND, OPENVIKING_PLUGIN_SOURCE } from "./decode.mjs";
import { deriveDshSessionId } from "./session-id.mjs";
import { addSkillExample, isSkillUri } from "./shared/uri-guard.mjs";

/** Model-facing name of a bridged OpenViking MCP tool. */
const mcp = rawName => `mcp__${MCP_SERVER_NAME}__${rawName}`;
const escapeText = value => String(value || "").replaceAll('"', '\\"');

const GUARDED_TOOLS = {
  read: { tool: mcp("read"), example: uri => `${mcp("read")}(uris="${uri}")` },
  glob: { tool: mcp("list"), example: uri => `${mcp("list")}(uri="${uri}")` },
  grep: {
    tool: mcp("grep"),
    example: (uri, args) => `${mcp("grep")}(pattern="${escapeText(args?.pattern)}", uri="${uri}")`,
  },
  bash: { tool: `${mcp("read")} or ${mcp("search")}`, example: uri => `${mcp("read")}(uris="${uri}")` },
  // On Windows DSH mounts `pwsh` in place of `bash`; its command line is also `command`.
  pwsh: { tool: `${mcp("read")} or ${mcp("search")}`, example: uri => `${mcp("read")}(uris="${uri}")` },
  edit: {
    tool: uri => (isSkillUri(uri) ? mcp("add_skill") : mcp("edit")),
    example: uri => (isSkillUri(uri)
      ? addSkillExample(uri, { call: mcp("add_skill"), edited: true })
      : `${mcp("edit")}(uri="${uri}", old_string="...", new_string="...")`),
  },
  write: {
    tool: uri => (isSkillUri(uri) ? mcp("add_skill") : mcp("write")),
    example: uri => (isSkillUri(uri)
      ? addSkillExample(uri, { call: mcp("add_skill") })
      : `${mcp("write")}(uri="${uri}", content="...")`),
  },
  str_replace_editor: { tool: "the OpenViking MCP tools", example: uri => `${mcp("read")}(uris="${uri}")` },
};

export function pluginMessage(content, source) {
  // dsh's own constructor: identity, normalization, and any future Message
  // invariants come from the pinned peer instead of a hand-built object.
  return createUserMessage({
    content: [{ type: "text", text: content }],
    source: { kind: OPENVIKING_PLUGIN_KIND, plugin: OPENVIKING_PLUGIN_SOURCE, ...source },
  });
}

/**
 * The plugin's DSH wiring builds one payload per event:
 * `{ session_id, cwd, subagent, resumed, idle, prompt, agent, signal, event, tool }`.
 *
 * @type {import("./shared/flow/host.d.mts").Host}
 */
const dsh = {
  id: "dsh",
  sessionPrefix: "dsh-",
  events: {
    "agent/created": "start",
    "agent/pre-step": "prompt",
    "session/event": "turnEnd",
    // DSH appends compaction/start before it summarizes and rewrites history.
    "compaction/start": "compactSoon",
    "session/dispose": "end",
    "tools/pre-execute": "toolCall",
  },
  budgets: { "session/dispose": 3000 },
  facts: {
    transcript: false,
    compactionDropsProfile: true,
    historySession: "default",
    historyTool: mcp("session_history"),
  },
  toolHints: GUARDED_TOOLS,
  read(event, payload) {
    const id = String(payload.session_id ?? "");
    return {
      sessionId: id,
      // Ids valid as a Windows path component, kept from before the flow.
      session: id ? deriveDshSessionId(id) : undefined,
      cwd: payload.cwd || undefined,
      child: payload.subagent ? "subagent" : undefined,
      prompt: event === "agent/pre-step" ? String(payload.prompt || "") : undefined,
      resumed: payload.resumed === "resume" ? "resume" : "",
      // Only an idle agent takes an injected message at startup.
      defer: event === "agent/created" && payload.idle === false,
    };
  },
  decode,
  // One host model call through the prompting agent's own LLM service.
  compress: (cfg, _log, ctx) => (ctx?.payload?.agent ? createRecallCompressor(ctx.payload.agent, cfg, ctx.payload.signal) : null),
  tool(payload) {
    return payload.tool ? { name: payload.tool.name, input: payload.tool.arguments } : null;
  },
  emit(event, output, payload = {}) {
    if (output.deny) return { kind: "deny", reason: output.deny };
    if (output.notice) {
      const uri = JSON.stringify(payload.tool?.arguments ?? "").match(/viking:\/\/[^\s"'`]*/)?.[0] || "a viking:// URI";
      return pluginMessage(output.notice, {
        form: "notice",
        summary: boundContextSummary(`OpenViking URI guard: shell command contains ${uri}`),
      });
    }
    if (!output.context) return null;
    return pluginMessage(output.context, { form: event === "agent/created" ? "instructions" : "recall" });
  },
};

export default dsh;
