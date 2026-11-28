import { toolCall } from "../shared/flow/index.mjs";
import pi from "../host.mjs";

/** pi's `tool_call` answer for the flow's decision: block the call, or fill session_history's session in place. */
export function guardVikingUriToolCall(event, sessionId = "") {
  const output = toolCall(pi, { ...event, sessionId }, "tool_call");
  if (output.deny) return { block: true, reason: output.deny };
  // pi runs the tool with this very object.
  if (output.input && sessionId && event?.input && typeof event.input === "object") Object.assign(event.input, output.input);
  return null;
}

// A tool_result handler's content replaces the result's content, so the
// original blocks are carried over and the notice is appended after them.
export function noticeVikingUriToolResult(event) {
  const { notice } = toolCall(pi, event, "tool_call");
  if (!notice) return null;
  const content = Array.isArray(event?.content) ? event.content : [];
  return { content: [...content, { type: "text", text: notice }] };
}
