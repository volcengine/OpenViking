import type { OVMessage } from "../client.js";
import { sanitizeToolUseResultPairing } from "../session-transcript-repair.js";

export type AgentMessage = {
  role?: string;
  content?: unknown;
  timestamp?: unknown;
};

export function toRoleId(senderId: string | undefined): string | undefined {
  if (!senderId) {
    return undefined;
  }
  const normalized = senderId
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
  return normalized || undefined;
}

/**
 * Convert an OpenViking stored message (parts-based format) into one or more
 * OpenClaw AgentMessages (content-blocks format).
 *
 * For assistant messages with ToolParts, this produces:
 * 1. The assistant message with canonical toolCall blocks in its content array
 * 2. A separate toolResult message per ToolPart (carrying tool_output)
 */
export function convertToAgentMessages(msg: { role: string; parts: unknown[] }): AgentMessage[] {
  const parts = msg.parts ?? [];
  const contentBlocks: Record<string, unknown>[] = [];
  const toolCallBlocks: Record<string, unknown>[] = [];
  const toolResults: AgentMessage[] = [];

  for (const part of parts) {
    if (!part || typeof part !== "object") continue;
    const p = part as Record<string, unknown>;

    if (p.type === "text" && typeof p.text === "string") {
      contentBlocks.push({ type: "text", text: p.text });
    } else if (p.type === "context") {
      if (typeof p.abstract === "string" && p.abstract) {
        contentBlocks.push({ type: "text", text: p.abstract });
      }
    } else if (p.type === "tool") {
      const toolId = typeof p.tool_id === "string" ? p.tool_id : "";
      const toolName = typeof p.tool_name === "string" ? p.tool_name : undefined;
      const status = typeof p.tool_status === "string" ? p.tool_status : "unknown";
      const output = typeof p.tool_output === "string" ? p.tool_output : "";
      const ref = typeof p.tool_output_ref === "string" ? p.tool_output_ref : "";
      const originalChars = typeof p.tool_output_original_chars === "number"
        ? p.tool_output_original_chars
        : undefined;

      if (toolId) {
        toolCallBlocks.push({
          type: "toolCall",
          id: toolId,
          name: toolName ?? "unknown",
          arguments: p.tool_input ?? {},
        });

        let resultText = (status === "completed" || status === "error")
          ? (output || "(no output)")
          : "(interrupted — tool did not complete)";
        if (ref && !resultText.includes(ref)) {
          const suffix = originalChars !== undefined
            ? `\n[tool-result-ref] ${ref} original_chars=${originalChars}`
            : `\n[tool-result-ref] ${ref}`;
          resultText += suffix;
        }
        const resultPayload: Record<string, unknown> = {
          role: "toolResult",
          toolCallId: toolId,
          content: [{ type: "text", text: resultText }],
          isError: status === "error",
        };
        if (toolName) {
          resultPayload.toolName = toolName;
        }
        toolResults.push(resultPayload as unknown as AgentMessage);
      } else {
        const fallbackName = toolName ?? "unknown";
        const segments = [`[${fallbackName}] (${status})`];
        if (p.tool_input) {
          try {
            segments.push(`Input: ${JSON.stringify(p.tool_input)}`);
          } catch {
            // non-serializable input, skip
          }
        }
        if (output) {
          segments.push(`Output: ${output}`);
        }
        contentBlocks.push({ type: "text", text: segments.join("\n") });
      }
    }
  }

  const result: AgentMessage[] = [];

  if (msg.role === "assistant") {
    result.push({ role: "assistant", content: [...contentBlocks, ...toolCallBlocks] });
    result.push(...toolResults);
  } else {
    const texts = contentBlocks
      .filter((b) => b.type === "text")
      .map((b) => b.text as string);
    if (texts.length > 0) {
      result.push({ role: msg.role, content: texts.join("\n") });
    } else if (toolCallBlocks.length === 0) {
      result.push({ role: msg.role, content: "" });
    }
    if (toolCallBlocks.length > 0) {
      result.push({ role: "assistant", content: toolCallBlocks });
      result.push(...toolResults);
    }
  }

  return result;
}

export function formatMessageFaithful(msg: OVMessage): string {
  const roleTag = `[${msg.role}]`;
  if (!msg.parts || msg.parts.length === 0) {
    return `${roleTag}: (empty)`;
  }

  const sections: string[] = [];
  for (const part of msg.parts) {
    if (!part || typeof part !== "object") continue;
    switch (part.type) {
      case "text":
        if (part.text) sections.push(part.text);
        break;
      case "tool": {
        const status = part.tool_status ?? "unknown";
        const header = `[Tool: ${part.tool_name ?? "unknown"}] (${status})`;
        const inputStr = part.tool_input
          ? `Input: ${JSON.stringify(part.tool_input, null, 2)}`
          : "";
        const outputStr = part.tool_output ? `Output:\n${part.tool_output}` : "";
        sections.push([header, inputStr, outputStr].filter(Boolean).join("\n"));
        break;
      }
      case "context":
        sections.push(
          `[Context: ${part.uri ?? "?"}]${part.abstract ? ` ${part.abstract}` : ""}`,
        );
        break;
      default:
        sections.push(`[${part.type}]: ${JSON.stringify(part)}`);
    }
  }

  return `${roleTag}:\n${sections.join("\n\n")}`;
}

/** Merge consecutive assistant messages by concatenating their content arrays. */
export function mergeConsecutiveAssistants(messages: AgentMessage[]): AgentMessage[] {
  const result: AgentMessage[] = [];
  for (const msg of messages) {
    const prev = result[result.length - 1];
    if (msg.role === "assistant" && prev?.role === "assistant") {
      const prevContent = Array.isArray(prev.content) ? prev.content : [{ type: "text", text: prev.content }];
      const currContent = Array.isArray(msg.content) ? msg.content : [{ type: "text", text: msg.content }];
      prev.content = [...prevContent, ...currContent] as typeof prev.content;
    } else {
      result.push({ ...msg });
    }
  }
  return result;
}

/**
 * Hoist tool_result blocks to the front of a content array.
 *
 * The Anthropic / Bedrock / Gemini APIs require tool_result blocks to appear
 * at the START of a user message's content array (a tool_result must follow
 * the assistant tool_use that produced it). When mergeConsecutiveUsers
 * merges two user messages, the previous content's text blocks may end up
 * before tool_results from the second message — this function fixes the order.
 *
 * Same pattern as Claude Code's hoistToolResults in src/utils/messages.ts.
 */
function hoistToolResults<T>(content: T[]): T[] {
  const toolResults: T[] = [];
  const others: T[] = [];
  for (const block of content) {
    if (
      block &&
      typeof block === "object" &&
      "type" in block &&
      (block as { type?: string }).type === "tool_result"
    ) {
      toolResults.push(block);
    } else {
      others.push(block);
    }
  }
  return [...toolResults, ...others];
}

/**
 * Merge consecutive user messages by concatenating their content arrays.
 *
 * Mirror of mergeConsecutiveAssistants. Required because Gemini and Anthropic
 * APIs reject consecutive same-role messages with stopReason=stop payloads=0
 * (empty response). Three independent sources can inject role: "user":
 *
 *   1. Archive commit: "[Session History Summary]" via buildArchiveMemory
 *   2. OpenClaw yield events: "[sessions_yield interrupt]" / "Turn yielded. ..."
 *   3. Audio/Telegram metadata: "[Audio] User text: [Telegram <name>...]"
 *
 * Without merging, these can stack into 2-5 consecutive user turns. The
 * 1P Anthropic API would merge server-side, but Bedrock/Gemini won't —
 * we merge client-side for wire-format consistency.
 *
 * Note: this MUST run AFTER sanitizeToolUseResultPairing because that pass
 * may strip orphaned tool_use / tool_result blocks and thereby create new
 * user-user adjacencies that didn't exist in the input.
 *
 * Tracks issue #1724.
 */
export function mergeConsecutiveUsers(messages: AgentMessage[]): AgentMessage[] {
  const result: AgentMessage[] = [];
  for (const msg of messages) {
    const prev = result[result.length - 1];
    if (msg.role === "user" && prev?.role === "user") {
      const prevContent = Array.isArray(prev.content) ? prev.content : [{ type: "text", text: prev.content }];
      const currContent = Array.isArray(msg.content) ? msg.content : [{ type: "text", text: msg.content }];
      prev.content = hoistToolResults([...prevContent, ...currContent]) as typeof prev.content;
    } else {
      result.push({ ...msg });
    }
  }
  return result;
}

/**
 * Defensive role-alternation invariant check.
 *
 * After mergeConsecutiveUsers + mergeConsecutiveAssistants, the message stream
 * should already alternate user/assistant. But sanitizeToolUseResultPairing
 * can in rare cases strip a user_with_tool_result message that was the only
 * thing separating two assistant messages, leaving an assistant-assistant
 * adjacency that upstream merge passes can't fix.
 *
 * When detected, we insert a placeholder user message — matching Claude Code's
 * NO_CONTENT_MESSAGE pattern (see CC src/utils/messages.ts:5375-5388) — to
 * preserve the alternation contract that Gemini / Anthropic require.
 */
export function ensureAlternation(messages: AgentMessage[]): AgentMessage[] {
  const result: AgentMessage[] = [];
  for (const msg of messages) {
    const prev = result[result.length - 1];
    if (prev && prev.role === "assistant" && msg.role === "assistant") {
      result.push({
        role: "user",
        content: "(no content)",
      });
    }
    result.push(msg);
  }
  return result;
}

function normalizeAssistantContent(messages: AgentMessage[]): void {
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (msg?.role === "assistant" && typeof msg.content === "string") {
      messages[i] = {
        ...msg,
        content: [{ type: "text", text: msg.content }],
      };
    }
  }
}

function canonicalizeAssistantBlock(block: unknown): unknown {
  if (!block || typeof block !== "object") {
    return block;
  }

  const rec = block as Record<string, unknown>;
  const type = typeof rec.type === "string" ? rec.type : "";
  if (type === "toolCall") {
    if (rec.arguments !== undefined) {
      return rec;
    }
    return {
      ...rec,
      arguments: rec.input ?? rec.toolInput ?? {},
    };
  }

  if (type === "toolUse" || type === "functionCall" || type === "tool_call") {
    return {
      type: "toolCall",
      id: rec.id ?? rec.toolCallId ?? rec.toolUseId,
      name: rec.name,
      arguments: rec.arguments ?? rec.input ?? rec.toolInput ?? {},
    };
  }

  return rec;
}

function canonicalizeAgentMessages(messages: AgentMessage[]): AgentMessage[] {
  let changed = false;
  const next = messages.map((msg) => {
    if (!msg || typeof msg !== "object") {
      return msg;
    }

    if (msg.role === "assistant") {
      const content = Array.isArray(msg.content)
        ? msg.content.map((block) => canonicalizeAssistantBlock(block))
        : typeof msg.content === "string"
          ? [{ type: "text", text: msg.content }]
          : msg.content;

      if (content !== msg.content) {
        changed = true;
        return { ...msg, content };
      }
      return msg;
    }

    if (msg.role === "toolResult") {
      const raw = msg as Record<string, unknown>;
      const toolCallId =
        (typeof raw.toolCallId === "string" && raw.toolCallId) ||
        (typeof raw.toolUseId === "string" && raw.toolUseId) ||
        undefined;
      const toolName =
        typeof raw.toolName === "string" && raw.toolName.trim()
          ? raw.toolName.trim()
          : undefined;

      const nextMsg = {
        ...msg,
        ...(toolCallId ? { toolCallId } : {}),
        ...(toolName ? { toolName } : {}),
      } as AgentMessage;

      if (nextMsg !== msg) {
        changed = true;
      }
      return nextMsg;
    }

    return msg;
  });

  return changed ? next : messages;
}

export function sanitizeAgentMessagesForProvider(messages: AgentMessage[]): AgentMessage[] {
  normalizeAssistantContent(messages);
  const canonical = canonicalizeAgentMessages(messages);
  // Defense in depth (issue #1724):
  //   1) sanitizeToolUseResultPairing may strip orphaned tool_use/tool_result,
  //      potentially creating new user-user or assistant-assistant adjacencies.
  //   2) mergeConsecutiveUsers fixes user-user (mirror of mergeConsecutiveAssistants
  //      already running inside buildSessionContext).
  //   3) ensureAlternation is a final invariant check for the rare
  //      assistant-assistant case that the merges can't reach.
  return ensureAlternation(
    mergeConsecutiveUsers(
      sanitizeToolUseResultPairing(canonical as never[]) as AgentMessage[],
    ),
  );
}
