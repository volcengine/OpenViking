/**
 * Seal secrets out of what Claude Code sends to the model, and give tools the
 * real values back: the Claude Code side of plugin sealing (`sealSecrets`).
 * The hooks module in `mods/seal/` calls it on prompt.submit (sealPrompt),
 * tool.call (unsealCall, then sealToolResult) and prompt.attachment
 * (sealAttachment). The requests, the response check, the deep walks and the
 * note live in the shared core (`scripts/shared/seal-core.mjs`); this module
 * adds what is Claude Code's own.
 *
 * When sealing cannot be done, what each function was about is stopped. The
 * mods runtime lets the original through when a hook throws, so a failure is
 * turned into an answer here (a drop, a deny, a withheld text). Pure: no Node
 * or engine API.
 */

import {
  SESSION_TEXT,
  SealError,
  createSealPolicy,
  isOpenVikingTool,
  sealNote,
  sealTexts,
  sealValue,
  stopHint,
  unsealValue,
  whyNotSealed,
  withheld,
} from "../shared/seal-core.mjs";

export { SESSION_TEXT, createSealPolicy, sealNote, withheld };

// The person waits on each call (a prompt, a tool), so it gets less than the
// general request timeout.
export const SEAL_TIMEOUT_MS = 10000;

/**
 * Tools that hand their arguments to a model: a subagent's prompt, a message
 * to another session, a workflow's agents, a skill's arguments. A value put
 * there would reach a model, so their arguments keep the references.
 */
export const AGENT_TOOLS = new Set(["Agent", "Task", "SendMessage", "Workflow", "Skill"]);

// What `tool.call`'s input carries beside the tool's own arguments.
const RESERVED_KEYS = new Set(["tool", "tool_use_id", "agentId", "requestMeta", "consent"]);

const SETTINGS_PLACE = "The settings go in the claude_code section of ~/.openviking/ovcli.conf, or in the environment.";

/** Why sealing cannot be done when OPENVIKING_SEAL_SECRETS is on but the setting helper gave no answer. */
export function settingUnknown() {
  return new SealError(
    "OPENVIKING_SEAL_SECRETS is on, but the plugin's settings could not be read here (the setting helper gave no answer)",
    { reason: "setting_unknown" },
  );
}

/** The message shown to the user when the prompt is stopped; `why` an error or a text. */
export function sealFailureMessage(why) {
  return `OpenViking did not send this prompt: ${whyNotSealed(why)}. ${stopHint(why)} ${SETTINGS_PLACE}`;
}

/** The reason the model reads when a tool call is blocked. */
export function unsealFailureMessage(why) {
  return (
    `OpenViking blocked this tool call: the secret values could not be put back in place of the references, because ${whyNotSealed(why)}. ` +
    "The tool did not run. Try again later, or ask the user to check the OpenViking server or the sealSecrets setting."
  );
}

/** What the model reads in place of an attachment that could not be sealed. */
export function withheldAttachment(why) {
  return (
    `[OpenViking withheld this attached content: sealing secrets in it failed (${why}). ` +
    "Its text is not shown so that no secret reaches the model.]"
  );
}

function messageOf(error) {
  return error instanceof Error ? error.message : String(error);
}

/**
 * The core's `post` over the runtime's `$.http.fetch`: `fetch(url, init)`
 * resolving `{ status, text }`, and `server` the `{ baseUrl, headers }` the
 * setting helper printed. A call that takes longer than `timeoutMs` is
 * reported as a timeout.
 */
export function serverPost(fetch, server, { timeoutMs = SEAL_TIMEOUT_MS } = {}) {
  return async (path, body) => {
    if (!server?.baseUrl) throw new SealError("no OpenViking server URL is configured");
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error("timed out"), { name: "TimeoutError" })), timeoutMs);
    });
    try {
      const res = await Promise.race([
        fetch(`${server.baseUrl}${path}`, {
          method: "POST",
          headers: { ...server.headers, "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
        timeout,
      ]);
      let json = null;
      try {
        json = JSON.parse(res.text);
      } catch {
        // Not JSON: the core reports the answer as malformed.
      }
      return { status: res.status, json };
    } finally {
      clearTimeout(timer);
    }
  };
}

function envFlag(value) {
  if (typeof value !== "string") return undefined;
  const v = value.trim().toLowerCase();
  if (["0", "false", "no", "off"].includes(v)) return false;
  if (["1", "true", "yes", "on"].includes(v)) return true;
  return undefined;
}

/** Whether OPENVIKING_SEAL_SECRETS explicitly turns sealing off: then no helper runs. */
export function envTurnsOff(envOverride) {
  return envFlag(envOverride) === false;
}

/**
 * The session's sealing mode: "on", "off" or "unknown".
 *
 * `setting` is what `seal-prompt.mjs` printed (`{ sealing: "on" | "off" }`),
 * or undefined when the helper could not answer. `envOverride` is
 * OPENVIKING_SEAL_SECRETS as the mod reads it: an explicit off value wins, so
 * a broken helper can always be switched off. Without an answer, sealing is
 * off (its default) unless the environment turns it on; only then is the mode
 * unknown, and the user told that it cannot run.
 */
export function sealingMode(setting, envOverride) {
  const env = envFlag(envOverride);
  if (env === false) return "off";
  const sealing = setting && typeof setting === "object" ? setting.sealing : undefined;
  if (sealing === "on" || sealing === "off") return sealing;
  return env === true ? "unknown" : "off";
}

/** Whether a prompt is one the seal looks at: not a slash command, meta or empty. */
export function isSealable(prompt) {
  const text = typeof prompt?.text === "string" ? prompt.text : "";
  return !prompt?.command && !prompt?.isMeta && Boolean(text.trim());
}

/**
 * Decide what happens to one `prompt.submit` while sealing is on.
 *
 * `prompt` is the event's input (`text`, and `command` for a slash command).
 */
export async function sealPrompt(prompt, { post }) {
  if (!isSealable(prompt)) return { kind: "pass" };
  try {
    const { texts } = await sealTexts(post, [{ text: prompt.text, enroll: true }]);
    const [item] = texts;
    if (!item.changed) return { kind: "pass" };
    const context = item.temporary.length ? [sealNote(item.temporary)] : [];
    return { kind: "rewrite", text: item.text, context };
  } catch (error) {
    return { kind: "drop", reason: sealFailureMessage(error) };
  }
}

/**
 * The tool call to run for one `tool.call` input: references in the tool's
 * arguments replaced by their values. OpenViking's own tools
 * and the tools in AGENT_TOOLS keep what the model wrote.
 *
 * Resolves `{ call, replaced }` (`call` is the input itself when nothing was
 * replaced) or `{ deny }` when a reference could not be resolved or checked.
 */
export async function unsealCall(post, input) {
  const args = {};
  for (const [key, value] of Object.entries(input)) if (!RESERVED_KEYS.has(key)) args[key] = value;
  const skip = isOpenVikingTool(input.tool) || AGENT_TOOLS.has(input.tool);
  try {
    const { value, replaced } = await unsealValue(post, args, { skip });
    return replaced ? { call: { ...input, ...value }, replaced } : { call: input, replaced: 0 };
  } catch (error) {
    return { deny: unsealFailureMessage(error) };
  }
}

/**
 * What the model gets for one tool result (what `next` resolved to in a
 * `tool.call` hook). Never throws.
 *
 * - A deny passes as it is.
 * - An error result: its text and context sealed, given back as one deny (the
 *   model reads a deny of an errored call as the error).
 * - A result: every string of the tool's record and of its context sealed,
 *   given back as the hook's own `{ result, context }`, which core maps for the
 *   model and records in the transcript. Unchanged, the original object
 *   (core then uses its own messages).
 * - Sealing failed: a deny with the withheld text.
 */
export async function sealToolResult(post, outcome, { enroll = false } = {}) {
  if (!outcome || typeof outcome !== "object" || outcome.deny !== undefined) return outcome;
  try {
    if (outcome.isError) {
      const text = typeof outcome.text === "string" ? outcome.text : typeof outcome.result === "string" ? outcome.result : "";
      const { value: sealed } = await sealValue(post, { text, context: outcome.context }, { enroll });
      if (sealed.text === text && sealed.context === outcome.context) return outcome;
      // A deny carries no context: what the model would read after the error follows it.
      return { deny: [sealed.text || withheld("the tool failed"), ...(sealed.context ?? [])].join("\n\n") };
    }
    const { value: sealed, sealed: count } = await sealValue(
      post,
      { result: outcome.result, context: outcome.context },
      { enroll },
    );
    if (!count) return outcome;
    return sealed.context?.length ? { result: sealed.result, context: sealed.context } : { result: sealed.result };
  } catch (error) {
    return { deny: withheld(messageOf(error)) };
  }
}

/**
 * The text the model reads for one `prompt.attachment` (a mentioned file, a
 * reminder, a hook's context), sealed without enrolling. Never throws: on a
 * failure it is the withheld text.
 */
export async function sealAttachment(post, text) {
  if (typeof text !== "string") return text;
  try {
    const { value } = await sealValue(post, text, { enroll: false });
    return value;
  } catch (error) {
    return withheldAttachment(messageOf(error));
  }
}
