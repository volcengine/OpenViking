import type { Tool } from "@modelcontextprotocol/client";

export const MCP_PROTOCOL_VERSION: "2025-06-18";
export const DEFAULT_TIMEOUT_MS: number;
export const DEFAULT_HANDSHAKE_BUDGET_MS: number;

export interface McpBridgeState {
  connected: boolean;
  closed: boolean;
  error: string | null;
  tools: Tool[];
}

export interface McpBridgeCallResult {
  content: ({ type: "text"; text: string } | { type: "image"; data: string; mimeType: string })[];
  details: { tool: string; truncated: boolean };
}

export interface McpBridge {
  connect(budgetMs?: number): Promise<McpBridgeState>;
  callTool(name: string, args: Record<string, unknown>, options?: { signal?: AbortSignal }): Promise<McpBridgeCallResult>;
  close(): Promise<void>;
  readonly state: McpBridgeState;
}

export function createMcpBridge(options: {
  readConfig: () => any;
  clientInfo?: { name: string; version: string };
}): McpBridge;
