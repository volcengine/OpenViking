/**
 * VS Code adapter for the MCP provider.
 *
 * The runtime registration API
 * (`vscode.lm.registerMcpServerDefinitionProvider`,
 * `vscode.McpHttpServerDefinition`) was added incrementally to VS Code
 * 1.99+ and may not be in every @types/vscode version we're compatible
 * with. We feature-detect both names defensively at register time —
 * when either is missing, we log a debug breadcrumb and return a
 * no-op disposable. The extension still loads cleanly; users on a
 * supported VS Code version get the MCP wiring.
 *
 * The provider re-reads cfg every time `provideMcpServerDefinitions`
 * is invoked so a workspace-settings change picked up by the
 * extension on the next activation flows through immediately.
 */

import * as vscode from "vscode";
import type { ActivationHandle } from "../extension-core";
import {
  MCP_PROVIDER_ID,
  buildOpenVikingMcpServerDefinition,
} from "./manifest";

interface DynamicLm {
  registerMcpServerDefinitionProvider?: (
    id: string,
    provider: { provideMcpServerDefinitions: () => unknown[] },
  ) => vscode.Disposable;
}

interface DynamicVscode {
  McpHttpServerDefinition?: new (name: string, uri: vscode.Uri, headers: Record<string, string>) => unknown;
}

export function registerOpenVikingMcpProvider(
  handle: ActivationHandle,
): vscode.Disposable {
  const lm = vscode.lm as unknown as DynamicLm;
  const reg = lm.registerMcpServerDefinitionProvider;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const McpHttp = (vscode as unknown as DynamicVscode).McpHttpServerDefinition;

  if (typeof reg !== "function" || typeof McpHttp !== "function") {
    handle.logger.log("mcp_provider_unavailable", {
      hasReg: typeof reg === "function",
      hasClass: typeof McpHttp === "function",
    });
    return new vscode.Disposable(() => {});
  }

  const provider = {
    provideMcpServerDefinitions: () => {
      const def = buildOpenVikingMcpServerDefinition(handle.cfg);
      handle.logger.log("mcp_definition_built", {
        uri: def.uri,
        headerCount: Object.keys(def.headers).length,
      });
      return [new McpHttp(def.name, vscode.Uri.parse(def.uri), def.headers)];
    },
  };
  return reg.call(lm, MCP_PROVIDER_ID, provider);
}
