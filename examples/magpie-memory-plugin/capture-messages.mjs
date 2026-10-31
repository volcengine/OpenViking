import { shapeCapturePayload } from "./shared/capture-utils.mjs";
import { recallAnchors } from "./recall-ledger.mjs";

/** Adapt only host formats; shared shaping strips recall, reminders and filters. */
export function captureMessages(messages, protocol, cfg) {
  const out = [], calls = new Set(), results = new Set(), names = {};
  const geminiCalls = new Map();
  let ambiguous = false;
  for (const [index, raw] of messages.entries()) {
    if (!raw || ["system", "developer"].includes(raw.role)) continue;
    let message = raw;
    if (protocol === "chat" && (raw.tool_calls || raw.function_call)) {
      if (raw.function_call) return null; // Legacy function calls have no stable call ID.
      message = { role: raw.role, content: [
        ...(typeof raw.content === "string" ? [{ type: "text", text: raw.content }] : raw.content || []),
        ...raw.tool_calls.map((call) => ({ type: "tool_use", id: call.id, name: call.function?.name, input: call.function?.arguments })),
      ] };
    }
    if (protocol === "gemini") {
      message = { role: raw.role === "model" ? "assistant" : raw.role, content: (raw.parts || []).flatMap((part, i) => {
        if (part.thought) return [];
        if (part.functionCall) {
          const call = part.functionCall;
          if (!call.id && geminiCalls.has(call.name)) ambiguous = true;
          const id = call.id || `gemini-${index}-${i}`;
          geminiCalls.set(call.name, id);
          return [{ type: "tool_use", id, name: call.name, input: call.args }];
        }
        if (part.functionResponse) {
          const result = part.functionResponse;
          const id = result.id || geminiCalls.get(result.name);
          geminiCalls.delete(result.name);
          return [{ type: "tool_result", tool_use_id: id, content: JSON.stringify(result.response) }];
        }
        return typeof part.text === "string" ? [{ type: "text", text: part.text }] : [];
      }) };
      if (ambiguous) return null;
    }
    const role = message.role === "assistant" || message.type === "function_call" ? "assistant" : "user";
    if (!["assistant", "user", "tool", "function"].includes(message.role) && !["message", "function_call", "function_call_output"].includes(message.type)) continue;
    const shaped = shapeCapturePayload(message, role, cfg, { toolNameById: names, faithful: true });
    const parts = shaped.parts.length ? shaped.parts : shaped.text ? [{ type: "text", text: shaped.text }] : [];
    if (!parts.length) continue;
    for (const part of parts.filter((p) => p.type === "tool")) {
      if (!part.tool_id) return null;
      if (part.tool_status === "running") { calls.add(part.tool_id); names[part.tool_id] = part.tool_name; }
      else results.add(part.tool_id);
    }
    out.push({ role, parts,
      message_kind: parts.every((p) => p.type === "tool") ? "tool_transport" : role === "user" ? "user_query" : "assistant_step",
      ...(cfg.peerId ? { peer_id: cfg.peerId } : {}),
    });
  }
  // An incomplete or orphaned tool exchange must not become an idle commit.
  if ([...calls].some((id) => !results.has(id)) || [...results].some((id) => !calls.has(id))) return null;
  const anchors = recallAnchors(out);
  return out.map((message, index) => ({ ...message, source_message_ids: [`magpie:${anchors[index]}`] }));
}

export function completedReply(reply, protocol) {
  if (!reply || typeof reply !== "object") return null;
  if (protocol === "chat") {
    const choice = reply.choices?.[0];
    return choice?.finish_reason === "stop" && choice.message?.role === "assistant" && choice.message?.content?.length
      && !choice.message.tool_calls?.length && !choice.message.function_call ? [choice.message] : null;
  }
  if (protocol === "anthropic") {
    return ["end_turn", "stop_sequence"].includes(reply.stop_reason) && reply.content?.some((p) => p.type === "text" && p.text?.trim()) && !reply.content.some((p) => p.type === "tool_use")
      ? [{ role: "assistant", content: reply.content }] : null;
  }
  if (protocol === "responses") {
    return reply.status === "completed" && Array.isArray(reply.output)
      && reply.output.some((p) => p.role === "assistant" && p.content?.some((part) => part.type === "output_text" && part.text?.trim()))
      && !reply.output.some((p) => p.type === "function_call") ? reply.output : null;
  }
  if (protocol === "gemini") {
    const candidate = reply.candidates?.[0];
    return candidate?.finishReason === "STOP" && candidate.content?.parts?.some((p) => !p.thought && p.text?.trim())
      && !candidate.content.parts.some((p) => p.functionCall) ? [candidate.content] : null;
  }
  return null;
}

/** Tool continuations are not failures. Persist only known unsuccessful ends. */
export function rejectedReply(reply, ctx) {
  if (ctx.success === false) return true;
  if (ctx.protocol === "chat") return ["length", "content_filter"].includes(reply?.choices?.[0]?.finish_reason);
  if (ctx.protocol === "anthropic") return ["max_tokens", "refusal", "model_context_window_exceeded"].includes(reply?.stop_reason);
  if (ctx.protocol === "responses") return ["failed", "incomplete", "cancelled"].includes(reply?.status);
  if (ctx.protocol === "gemini") {
    const reason = reply?.candidates?.[0]?.finishReason;
    return Boolean(reason && !["STOP", "FINISH_REASON_UNSPECIFIED"].includes(reason));
  }
  return false;
}
