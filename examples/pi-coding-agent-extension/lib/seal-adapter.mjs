/**
 * Plugin-side sealing for pi (`sealSecrets`, `enrollToolSecrets`).
 *
 * The shared core (`shared/seal-core.mjs`) talks to the server and checks its
 * answers; this file decides where pi calls it and what happens there when
 * sealing cannot be done: the server is too old for it or its
 * version cannot be read (asked before any seal call), or a call failed.
 * Then what it was about is stopped:
 *
 * - `input`: the user's text is sealed with `enroll: true` before pi stores it
 *   or sends it. A turn whose text got a temporary reference gets the note
 *   through `before_agent_start`. On failure the input is stopped and the user
 *   is told why; a throwing `input` handler would let the text through, so
 *   everything is caught here.
 * - `tool_call`: references the model wrote are resolved in `event.input`, in
 *   place. pi hands extensions a copy of the arguments, so the transcript keeps
 *   what the model wrote. A failure blocks the call with a reason.
 * - `tool_result`: the text content, `details` (persisted) and
 *   `structuredContent` are sealed. On failure they are replaced with the
 *   withheld text and the result is marked as an error; a throwing
 *   `tool_result` handler would let the original through, so this one does not
 *   throw.
 * - `before_agent_start`: the session text.
 *
 * Nothing here logs a text, a value or a server answer.
 */
import {
  createSealPolicy,
  sealTexts,
  sealValue,
  sealNote,
  unsealValue,
  stopHint,
  withheld,
  whyNotSealed,
  isOpenVikingTool,
  SESSION_TEXT,
} from "../shared/seal-core.mjs";

/** The custom message type that carries the note of a turn. */
export const SEAL_NOTE_TYPE = "openviking-seal-note";

/**
 * Tools that hand their arguments to another model. Pi has none built in; its
 * example subagent extension registers `subagent`. Unsealing there would send
 * the plain value to a model, so their arguments keep the references.
 */
export const AGENT_TOOLS = new Set(["subagent", "agent"]);

/** What the model is told for the whole session while sealing is on: the core's text. */
export const SEAL_SESSION_TEXT = SESSION_TEXT;

/** Seal and resolve calls can carry megabytes; give them more than a health check. */
const SEAL_TIMEOUT_MS = 30000;

/**
 * The core's `post` over the extension's OpenViking client.
 *
 * `fetchJSON` (`shared/ov-http.mjs`) never throws: status 0 means no answer
 * arrived, which the core needs as a rejection (a timeout marked as one). Any
 * other answer is handed back as the server's envelope.
 */
export function createSealPost(fetchJSON, { timeoutMs = SEAL_TIMEOUT_MS } = {}) {
  return async (path, body) => {
    const res = await fetchJSON(path, { method: "POST", body: JSON.stringify(body) }, { timeoutMs });
    if (!res || !res.status) {
      const error = new Error(res?.error?.message || "no answer");
      if (res?.error?.aborted || res?.error?.name === "AbortError") {
        error.name = "AbortError";
        error.aborted = true;
      }
      throw error;
    }
    const json = res.ok ? { status: "ok", result: res.result } : { status: "error", error: res.error };
    return { status: res.status, json };
  };
}

function reasonOf(error) {
  return error instanceof Error && error.message ? error.message : String(error || "unknown error");
}

function stoppedInput(error) {
  return `OpenViking: your message was not sent, because ${whyNotSealed(error)}. ${stopHint(error)}`;
}

/**
 * The four handlers, for `index.ts` to register in the order the other
 * handlers on the same events need.
 *
 * `ownToolNames()` lists the tools this extension registered (they count as
 * OpenViking tools). `tell(ctx, message)` reaches the user: a notification in
 * the TUI, stderr where pi has no UI. `check()` answers whether the server
 * supports sealing (`featureStatus("privacy_seal", version)` of
 * `shared/server-features.mjs`); a yes is kept for the pi session, a no is
 * asked again.
 */
export function createSealHandlers({
  post,
  enrollToolSecrets = false,
  check,
  ownToolNames = () => [],
  tell = () => {},
}) {
  // The note owed to the run that the last sealed input starts.
  let pending = null;
  // Every seal and resolve call: none while the server cannot seal.
  const send = createSealPolicy({ check }).guard(post);

  async function onInput(event, ctx) {
    const text = typeof event?.text === "string" ? event.text : "";
    // Empty input passes untouched. Text starting with "/" is sealed too: pi
    // runs extension commands before `input`, so whatever reaches it here
    // (`/skill:…` and template arguments, or plain text) goes to the model.
    if (!text.trim()) return { action: "continue" };
    try {
      const { texts } = await sealTexts(send, [{ text, enroll: true }]);
      const sealed = texts[0];
      pending = null;
      if (!sealed.changed) return { action: "continue" };
      let out = sealed.text;
      if (sealed.temporary.length) {
        const note = sealNote(sealed.temporary);
        // A message queued into a running turn never reaches
        // before_agent_start, so its note travels with it.
        if (event.streamingBehavior) out = `${out}\n\n${note}`;
        else pending = { references: sealed.temporary, note };
      }
      return { action: "transform", text: out, images: event.images };
    } catch (error) {
      pending = null;
      try {
        tell(ctx, stoppedInput(error));
      } catch {
        // A throw here would let the unsealed text through; the input stays stopped.
      }
      return { action: "handled" };
    }
  }

  async function onBeforeAgentStart(event) {
    const result = { systemPrompt: `${event.systemPrompt}\n\n${SEAL_SESSION_TEXT}` };
    const owed = pending;
    pending = null;
    // pi expands `/skill:…` and templates after `input`, so the prompt here may
    // differ from the sealed text; it still holds the run's references.
    const prompt = String(event.prompt ?? "");
    if (owed && event.messageType !== "custom" && owed.references.some((reference) => prompt.includes(reference))) {
      result.message = { customType: SEAL_NOTE_TYPE, content: owed.note, display: false };
    }
    return result;
  }

  async function onToolCall(event) {
    const name = event?.toolName;
    const input = event?.input;
    if (!input || typeof input !== "object") return;
    const skip = isOpenVikingTool(name, ownToolNames()) || AGENT_TOOLS.has(name);
    if (skip) return;
    try {
      const { value, replaced } = await unsealValue(send, input);
      // pi runs the tool with this very object; the transcript holds its own copy.
      if (replaced) Object.assign(input, value);
      return;
    } catch (error) {
      return {
        block: true,
        reason: `OpenViking could not put the secret values in for the references in this call: ${reasonOf(error)}. ` +
          `The tool did not run. ${stopHint(error)}`,
      };
    }
  }

  async function onToolResult(event) {
    const content = Array.isArray(event?.content) ? event.content : [];
    // Images are not text and stay as they are; only text blocks are sealed.
    const texts = content.map((block) => (block?.type === "text" && typeof block.text === "string" ? block.text : null));
    const whole = { texts, details: event?.details ?? null, structured: event?.structuredContent ?? null };
    try {
      const { value, sealed } = await sealValue(send, whole, { enroll: enrollToolSecrets === true });
      if (!sealed) return;
      const patch = {};
      if (value.texts !== whole.texts) {
        patch.content = content.map((block, i) =>
          value.texts[i] !== texts[i] ? { ...block, text: value.texts[i] } : block,
        );
        // Replacing content drops structuredContent unless it comes back too.
        if (event.structuredContent !== undefined) patch.structuredContent = value.structured;
      }
      if (value.details !== whole.details) patch.details = value.details;
      if (value.structured !== whole.structured) patch.structuredContent = value.structured;
      return patch;
    } catch (error) {
      return {
        content: [{ type: "text", text: withheld(reasonOf(error)) }],
        // pi ignores an undefined patch field, so an empty object clears them.
        details: {},
        isError: true,
      };
    }
  }

  return { onInput, onBeforeAgentStart, onToolCall, onToolResult };
}
