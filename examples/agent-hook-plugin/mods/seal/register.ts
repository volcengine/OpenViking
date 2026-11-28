import type { EngineInterface, On } from "claude-code";
import {
  createSealPolicy,
  envTurnsOff,
  isSealable,
  sealAttachment,
  sealFailureMessage,
  sealingMode,
  sealPrompt,
  sealToolResult,
  serverPost,
  SESSION_TEXT,
  settingUnknown,
  unsealCall,
  unsealFailureMessage,
  withheld,
  withheldAttachment,
  type SealingMode,
  type SealPost,
  type SealSetting,
} from "../../hosts/claude-code/prompt-seal.mjs";

// Reading the setting asks the server's /health once (3 s at most).
const SETTING_TIMEOUT_MS = 5_000;

const HELPER = "hosts/claude-code/seal-prompt.mjs";

async function envSetting($: EngineInterface): Promise<string | undefined> {
  return $.env.get("OPENVIKING_SEAL_SECRETS").catch(() => undefined);
}

// The setting, the connection and the server's support as the helper reads
// them (the plugin's config chain and the feature table, which a hooks module
// cannot import); undefined when the helper gives no answer.
async function readSetting($: EngineInterface): Promise<SealSetting | undefined> {
  try {
    const run = await $.process.run(["node", `${$.plugin.root}/${HELPER}`], {
      env: await $.process.hookEnv(),
      timeoutMs: SETTING_TIMEOUT_MS,
    });
    return run.exitCode === 0 ? (JSON.parse(run.stdout) as SealSetting) : undefined;
  } catch {
    // No answer, e.g. a surface without $.process (the Desktop Code tab, VS Code).
    return undefined;
  }
}

type Session = { mode: SealingMode; enrollTools: boolean; post: SealPost };

// Session id -> what the session seals with, so `/clear` (a new id, no
// session.start) reads it again. A promise, so hooks racing at the start of a
// session run the helper once.
const sessions = new Map<string, Promise<Session>>();

async function readSession($: EngineInterface): Promise<Session> {
  const env = await envSetting($);
  // An explicit OPENVIKING_SEAL_SECRETS=0 needs no helper at all.
  const setting = envTurnsOff(env) ? undefined : await readSetting($);
  const mode = sealingMode(setting, env);
  const on = setting?.sealing === "on" ? setting : undefined;
  // Unknown (the environment turns sealing on, the helper gave no answer) is
  // one more reason sealing cannot be done, answered like a failed call.
  const policy = createSealPolicy({
    check: () => (mode === "unknown" ? settingUnknown() : on?.support ?? { supported: false }),
  });
  // Every seal and resolve call goes to the server directly from here, and
  // none while the policy says the server cannot seal.
  const post = policy.guard(serverPost((url, init) => $.http.fetch(url, init), on?.server));
  return { mode, enrollTools: Boolean(on?.enrollToolSecrets), post };
}

// Never throws: a helper that gives no answer is a setting of undefined.
async function session($: EngineInterface): Promise<Session> {
  const id = await $.session.id().catch(() => "");
  let known = sessions.get(id);
  if (!known) {
    known = readSession($);
    sessions.set(id, known);
  }
  return known;
}

// Session ids whose conversation holds SESSION_TEXT. A compaction takes it
// out, and a reload of this module forgets them: the next sealed prompt adds
// it again.
const told = new Set<string>();

// What a catch handler acts on: whether the hook's failure stops what it was
// about, which it does while sealing is on.
async function stopsOnFailure($: EngineInterface): Promise<boolean> {
  const known = sessions.get(await $.session.id().catch(() => ""));
  const s = known ? await known : undefined;
  return Boolean(s && s.mode !== "off");
}

function why(kind: string, what: string) {
  return kind === "timeout" ? `the ${what} timed out` : `the ${what} failed`;
}

// Replaces secrets with {{ov_privacy:…}} references in what the model reads,
// and gives tools the values back, when `sealSecrets` is on.
// The setting is read once per session (a changed setting takes effect in a
// new one), and again after a stopped prompt. Off: after the one setting read, everything passes untouched. A
// helper that gives no answer means off, unless OPENVIKING_SEAL_SECRETS turns
// sealing on. When sealing cannot be done (that case, the server too old or
// unreachable, a call failed), every such failure stops what it was about,
// since the runtime would otherwise let the original through.
export function registerSeal(on: On) {
  // The prompt: sealed with enroll; the note rides along for temporary
  // references, and the session text with the first sealed prompt.
  on("prompt.submit", async ($, e, next) => {
    if (!isSealable(e)) return next(e);
    const s = await session($);
    if (s.mode === "off") return next(e);
    const decision = await sealPrompt(e, { post: s.post });
    const id = await $.session.id().catch(() => "");
    // A server that cannot seal drops the prompt here, so the session text
    // below only reaches a session that seals. The next prompt reads the
    // setting and the server's version again.
    if (decision.kind === "drop") {
      sessions.delete(id);
      return { drop: decision.reason };
    }
    const tell = !told.has(id);
    if (tell) told.add(id);
    if (decision.kind === "pass" && !tell) return next(e);
    return next({
      ...e,
      text: decision.kind === "rewrite" ? decision.text : e.text,
      context: [
        ...(e.context ?? []),
        ...(decision.kind === "rewrite" ? decision.context : []),
        ...(tell ? [SESSION_TEXT] : []),
      ],
    });
  }).catch(async ($, e, next) => {
    if (next.called) return undefined;
    const reason = why(next.error.kind, "seal hook");
    if (!(await stopsOnFailure($))) return next(e);
    return { drop: sealFailureMessage(reason) };
  });

  // A tool call: the tool gets the values for the references the model wrote
  // (the transcript keeps what it wrote), and the model gets the result sealed.
  // The matcher takes every tool; it is there because the usage mod hooks
  // tool.call without one, and a plugin may do that once.
  on("tool.call", { tool: /./ }, async ($, e, next) => {
    const s = await session($);
    if (s.mode === "off") return next(e);
    const unsealed = await unsealCall(s.post, e);
    if (unsealed.deny !== undefined) return { deny: unsealed.deny };
    const outcome = await next(unsealed.call);
    // A throw past this point would let the original result stand, so
    // sealToolResult never throws.
    return sealToolResult(s.post, outcome, { enroll: s.enrollTools });
  }).catch(async ($, e, next) => {
    // Calling next again would run the tool a second time: after it ran, no
    // answer but a deny changes its result.
    const reason = why(next.error.kind, "seal hook");
    if (!(await stopsOnFailure($))) return next.called ? undefined : next(e);
    if (next.called) return { deny: withheld(reason) };
    return { deny: unsealFailureMessage(reason) };
  });

  // The main conversation's summary drops the session text (a precompute
  // installs nothing).
  on("session.compact", async ($, e, next) => {
    const done = await next(e);
    if (!e.agentId && e.trigger !== "precompute" && !done.skip) {
      told.delete(await $.session.id().catch(() => ""));
    }
    return done;
  });

  // What the engine injects for the model: a mentioned file, a reminder, a
  // hook's context. Sealed without enrolling; the transcript keeps the
  // engine's record.
  on("prompt.attachment", async ($, e, next) => {
    const s = await session($);
    if (s.mode === "off") return next(e);
    const text = await sealAttachment(s.post, e.text);
    return text === e.text ? next(e) : next({ ...e, text });
  }).catch(async ($, e, next) => {
    if (next.called) return undefined;
    const reason = why(next.error.kind, "seal hook");
    if (!(await stopsOnFailure($))) return next(e);
    return { text: withheldAttachment(reason) };
  });
}
