import * as mcpClient from "@deepseek-ai/dsh-mcp-client";

import { MCP_SERVER_NAME } from "./config.mjs";
import { buildMcpConfig } from "./mcp-env.mjs";
import { historySessionIds } from "./session-id.mjs";

export { buildMcpConfig, PROXY_PATH } from "./mcp-env.mjs";

const SESSION_HISTORY_TOOL = `mcp__${MCP_SERVER_NAME}__session_history`;

/**
 * Mount the tool surface. The bridge ships with dsh itself, so it resolves
 * from the host installation and never needs a separate install step.
 * Startup failure is contained: recall, capture, and commit keep working
 * against a server whose MCP endpoint is unreachable.
 */
export function mountOpenVikingMcp(ctx, config) {
  return ctx.plugin(mcpClient, buildMcpConfig(config));
}

/**
 * `tools/execute` wrapper that defaults `session_history`'s `session` to the
 * calling agent's OpenViking session. The proxy is shared by every session and
 * the bridge forwards no caller identity, while DSH freezes call arguments, so
 * a call without `session` is answered here by running the bridged tool with it
 * added. A session the model passed is left alone.
 */
export async function fillHistorySession(tools, exec, next, sessions) {
  const args = exec.arguments && typeof exec.arguments === "object" ? exec.arguments : {};
  const given = args.session;
  const session = exec.agent?.session;
  if (
    exec.name !== SESSION_HISTORY_TOOL
    || session?.id == null
    || (given != null && (typeof given !== "string" || given.trim()))
  ) {
    return next();
  }
  const tool = tools.get(exec.name, exec.agent);
  if (!tool) return next();
  try {
    const value = await tool.execute({ ...args, session: historySessionIds(session, sessions) }, exec);
    return { isError: false, value, content: [] };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { isError: true, error: { message }, content: [{ type: "text", text: `Error: ${message}` }] };
  }
}
