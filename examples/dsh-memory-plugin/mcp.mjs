import * as mcpClient from "@deepseek-ai/dsh-mcp-client";

import { buildMcpConfig } from "./mcp-env.mjs";

export { buildMcpConfig, PROXY_PATH } from "./mcp-env.mjs";

/**
 * Mount the tool surface. The bridge ships with dsh itself, so it resolves
 * from the host installation and never needs a separate install step.
 * Startup failure is contained: recall, capture, and commit keep working
 * against a server whose MCP endpoint is unreachable.
 */
export function mountOpenVikingMcp(ctx, config) {
  return ctx.plugin(mcpClient, buildMcpConfig(config));
}
