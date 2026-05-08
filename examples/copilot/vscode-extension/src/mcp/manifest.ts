/**
 * Build the MCP server definition the extension surfaces to Copilot
 * Chat. We use VS Code's runtime
 * `vscode.lm.registerMcpServerDefinitionProvider` API (registered in
 * `register.ts`) rather than a static `.mcp.json` because the
 * connection details live in PluginConfig, which the extension
 * already resolves with the env > host > ovcli.conf > ov.conf
 * priority chain. The dynamic provider lets us inject the *resolved*
 * apiKey + tenant headers — no `${VAR}` substitution dance, no
 * leaking secrets into a JSON file on disk.
 *
 * Local-only mode (no apiKey, no tenant fields): headers are an
 * empty object. The OpenViking server's `/mcp` endpoint accepts
 * unauthenticated calls when bound to 127.0.0.1.
 *
 * Remote / multi-tenant mode: Authorization + every populated
 * X-OpenViking-* header is attached.
 */

import type { PluginConfig } from "@openviking/copilot-shared";

/** Stable identifier matched against the manifest's contributes entry. */
export const MCP_PROVIDER_ID = "openviking" as const;

/**
 * Structural mirror of `vscode.McpHttpServerDefinition`'s constructor
 * args. Decouples this file from the vscode types so it stays
 * unit-testable under Vitest.
 */
export interface McpHttpServerDefinitionLike {
  /** Display name surfaced in the Copilot Chat UI. */
  name: string;
  /** Absolute URL of OV's HTTP MCP endpoint. */
  uri: string;
  /** Headers attached to every MCP request. Empty in local-only mode. */
  headers: Record<string, string>;
}

/**
 * Build the HTTP MCP server definition from a resolved PluginConfig.
 * Headers mirror exactly what `OVClient.buildHeaders()` sends so
 * Copilot Chat's MCP traffic and the extension's REST traffic land
 * with the same identity on the OpenViking server.
 */
export function buildOpenVikingMcpServerDefinition(
  cfg: PluginConfig,
): McpHttpServerDefinitionLike {
  const baseUrl = cfg.baseUrl.replace(/\/+$/, "");
  const headers: Record<string, string> = {};
  if (cfg.apiKey) headers["Authorization"] = `Bearer ${cfg.apiKey}`;
  if (cfg.accountId) headers["X-OpenViking-Account"] = cfg.accountId;
  if (cfg.userId) headers["X-OpenViking-User"] = cfg.userId;
  if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId;

  return {
    name: "OpenViking",
    uri: `${baseUrl}/mcp`,
    headers,
  };
}
