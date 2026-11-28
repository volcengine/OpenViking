import type { SealPost, SealSupport } from "../shared/seal-core.mjs";

export const SEAL_NOTE_TYPE: "openviking-seal-note";
export const AGENT_TOOLS: Set<string>;
export const SEAL_SESSION_TEXT: string;

export function createSealPost(
  fetchJSON: (path: string, init?: RequestInit, options?: { timeoutMs?: number }) => Promise<any>,
  options?: { timeoutMs?: number },
): SealPost;

export function createSealHandlers(options: {
  post: SealPost;
  enrollToolSecrets?: boolean;
  /** Whether the server supports sealing; asked until it does. Unset: assumed supported. */
  check?: () => SealSupport | Promise<SealSupport>;
  ownToolNames?: () => readonly string[];
  tell?: (ctx: any, message: string) => void;
}): {
  onInput(event: any, ctx: any): Promise<{ action: "continue" } | { action: "transform"; text: string; images?: any[] } | { action: "handled" }>;
  onBeforeAgentStart(event: any): Promise<{ systemPrompt: string; message?: { customType: string; content: string; display: boolean } }>;
  onToolCall(event: any): Promise<{ block: true; reason: string } | undefined>;
  onToolResult(event: any): Promise<{ content?: any[]; details?: unknown; structuredContent?: unknown; isError?: boolean } | undefined>;
};
