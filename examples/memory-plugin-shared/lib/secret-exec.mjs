/**
 * Secret exec: a shell command names a saved credential by its reference, and
 * the value exists only in the environment of the process that runs it.
 *
 *   model writes  curl -H "Authorization: Bearer {{ov_privacy:...}}" https://...
 *   hook rewrites '<node>' '<runner>' 'curl -H "Authorization: Bearer {{ov_privacy:...}}" https://...'
 *   runner        resolves each reference, runs the command with ${OV_SECRET_0}
 *                 where the reference stood, and masks the values in the output
 *                 back to their references
 *
 * The hook half never touches the network and never sees a value, so a hook
 * failure leaves the references in place and the command fails rather than
 * leaks. The runner half takes every effect as an argument.
 */

import { constants } from "node:os";
import { basename } from "node:path";
import { Transform } from "node:stream";

// Canonical references only. Every segment of one is percent-encoded, so a
// match never holds a quote, a `$` or a blank, and swapping it for an
// expansion cannot move the quoting around it.
const REFERENCE = /\{\{[ \t]*ov_privacy:[\w.@-]+:viking:\/\/~\/privacy\/credential\/([\w.@~%-]+)#\/values\/([\w.@~%-]+)[ \t]*\}\}/;

const canonical = (reference) => reference.replace(/^\{\{[ \t]*/, "{{").replace(/[ \t]*\}\}$/, "}}");

/** Whether `text` holds a credential reference at all. */
export function hasReference(text) {
  return REFERENCE.test(String(text ?? ""));
}

// The variable is always expanded, never pasted: an expansion's result is not
// parsed again as shell syntax, so no value can become code. A context the
// scanner misreads costs a wrongly quoted expansion or a literal
// `${OV_SECRET_n}`; the value stays in the environment either way.
const EXPANSION = {
  plain: (name) => `"${name}"`,
  double: (name) => name,
  single: (name) => `'"${name}"'`,
  ansi: (name) => `'"${name}"$'`,
  heredoc: (name) => name,
};

const QUOTED_HEREDOC = "a credential reference cannot be used in a here-document whose delimiter is quoted "
  + "(<<'EOF'), because the shell never expands it there. To use the value, leave the delimiter unquoted (<<EOF); "
  + "to write the reference itself as text, use a file-editing tool instead of the shell.";

/** One here-document operator at `at` (just past `<<`): where it ends, and its delimiter. */
function readHeredoc(command, at) {
  let i = at;
  const strip = command[i] === "-";
  if (strip) i += 1;
  while (command[i] === " " || command[i] === "\t") i += 1;
  let word = "";
  let quoted = false;
  while (i < command.length && !/[\s;&|<>()]/.test(command[i])) {
    const c = command[i];
    if (c === "'" || c === '"') {
      const close = command.indexOf(c, i + 1);
      if (close < 0) break;
      word += command.slice(i + 1, close);
      quoted = true;
      i = close + 1;
    } else if (c === "\\") {
      word += command[i + 1] ?? "";
      quoted = true;
      i += 2;
    } else {
      word += c;
      i += 1;
    }
  }
  return { end: i, doc: word ? { word, strip, expand: !quoted } : null };
}

/**
 * Rewrite every reference in `command` into an expansion of `OV_SECRET_<n>`,
 * written for the quoting it sits in. Returns `{ script, references }`, where
 * `references[n]` is what `OV_SECRET_<n>` must hold, or `{ error }`.
 *
 * The scanner tracks backslashes, the three kinds of quotes, comments,
 * here-documents and, best effort, `$( )` and backticks.
 */
export function rewriteCommand(command) {
  const references = [];
  const expand = (kind, reference) => {
    const key = canonical(reference);
    let n = references.indexOf(key);
    if (n < 0) n = references.push(key) - 1;
    return EXPANSION[kind](`\${OV_SECRET_${n}}`);
  };
  const sticky = new RegExp(REFERENCE.source, "y");
  const everywhere = new RegExp(REFERENCE.source, "g");
  // `plain` is unquoted shell: the top level, or the inside of `$( )` or backticks.
  const stack = [{ kind: "plain" }];
  const heredocs = [];
  let script = "";
  let i = 0;
  const take = (n) => {
    script += command.slice(i, i + n);
    i += n;
  };

  while (i < command.length) {
    const frame = stack.at(-1);
    sticky.lastIndex = i;
    const reference = sticky.exec(command)?.[0];
    if (reference) {
      script += expand(frame.kind, reference);
      i += reference.length;
      continue;
    }
    const c = command[i];
    if (frame.kind === "single") {
      if (c === "'") stack.pop();
      take(1);
    } else if (c === "\\") {
      take(2);
    } else if (frame.kind === "ansi") {
      if (c === "'") stack.pop();
      take(1);
    } else if (command.startsWith("$(", i)) {
      stack.push({ kind: "plain", close: ")", depth: 0 });
      take(2);
    } else if (c === "`") {
      if (frame.close === "`") stack.pop();
      else stack.push({ kind: "plain", close: "`" });
      take(1);
    } else if (frame.kind === "double") {
      if (c === '"') stack.pop();
      take(1);
    } else if (c === "'" || c === '"') {
      stack.push({ kind: c === "'" ? "single" : "double" });
      take(1);
    } else if (command.startsWith("$'", i)) {
      stack.push({ kind: "ansi" });
      take(2);
    } else if (frame.close === ")" && (c === "(" || c === ")")) {
      if (c === "(") frame.depth += 1;
      else if (frame.depth) frame.depth -= 1;
      else stack.pop();
      take(1);
    } else if (c === "#" && (i === 0 || /[\s;&|()<>`]/.test(command[i - 1]))) {
      const eol = command.indexOf("\n", i);
      take((eol < 0 ? command.length : eol) - i);
    } else if (command.startsWith("<<<", i)) {
      take(3);
    } else if (command.startsWith("<<", i)) {
      const { end, doc } = readHeredoc(command, i + 2);
      if (doc) heredocs.push(doc);
      take(end - i);
    } else if (c === "\n" && heredocs.length) {
      take(1);
      for (const doc of heredocs.splice(0)) {
        while (i < command.length) {
          const eol = command.indexOf("\n", i);
          const end = eol < 0 ? command.length : eol + 1;
          const line = command.slice(i, end);
          const text = line.endsWith("\n") ? line.slice(0, -1) : line;
          if ((doc.strip ? text.replace(/^\t+/, "") : text) === doc.word) {
            take(end - i);
            break;
          }
          if (!doc.expand && REFERENCE.test(line)) return { error: QUOTED_HEREDOC };
          script += doc.expand ? line.replace(everywhere, (found) => expand("heredoc", found)) : line;
          i = end;
        }
      }
    } else {
      take(1);
    }
  }
  return { script, references };
}

/** POSIX single-quoting: the one quoting in which nothing is special. */
export function shellQuote(text) {
  return `'${String(text).replaceAll("'", "'\\''")}'`;
}

/** `'<node>' '<runner>' '<command>'`, the command intact so a permission prompt shows what will run. */
export function wrapCommand(command, { node, runner }) {
  return [node, runner, command].map(shellQuote).join(" ");
}

// Any install's runner counts, not only this one: an update moves the plugin
// root, and a command wrapped twice would hand the inner runner its values as
// command text.
const WRAPPED = /^'[^']*' '[^']*\/([^/']+)' '/;

export function isWrapped(command, { runner }) {
  return String(command).match(WRAPPED)?.[1] === basename(String(runner));
}

const decode = (segment) => {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
};

/** `github/pat` for a reference to field `pat` of target `github`. */
export function credentialName(reference) {
  const [, target, field] = String(reference).match(REFERENCE) || [];
  return target ? `${decode(target)}/${decode(field)}` : "";
}

const RESOLVE_TOOL = /__privacy_resolve$/;

const RESOLVE_DENIED = "OpenViking secret exec is on, so credential values are not handed to the model. "
  + "Write the reference itself, {{ov_privacy:...}}, verbatim into the Bash command that needs it: "
  + "the value is put in place when the command runs and masked in its output.";

const preToolUse = (fields) => ({ hookSpecificOutput: { hookEventName: "PreToolUse", ...fields } });

/**
 * The PreToolUse answer for one tool call, or null to leave it alone.
 *
 * A Bash command that names a credential is wrapped in the runner, with every
 * other input field kept. No decision is given, so the wrapper goes through the
 * user's own permission flow, unless `secretExecApproval` is `ask`. It is never
 * `allow`: a reference would then be a way past approval for any command.
 * `privacy_resolve` is denied: it would put the value in the conversation.
 */
export function secretExecPreToolUse(event = {}, cfg = {}, wrapper = {}) {
  if (!cfg.secretExec) return null;
  const toolName = String(event.tool_name ?? "");
  if (RESOLVE_TOOL.test(toolName)) {
    return preToolUse({ permissionDecision: "deny", permissionDecisionReason: RESOLVE_DENIED });
  }
  const input = event.tool_input;
  const command = input?.command;
  if (toolName !== "Bash" || typeof command !== "string" || !hasReference(command)) return null;
  if (isWrapped(command, wrapper)) return null;

  const plan = rewriteCommand(command);
  if (plan.error) {
    return preToolUse({ permissionDecision: "deny", permissionDecisionReason: `OpenViking secret exec: ${plan.error}` });
  }
  if (!plan.references.length) return null;
  return preToolUse({
    ...(cfg.secretExecApproval === "ask" ? { permissionDecision: "ask" } : {}),
    permissionDecisionReason: `OpenViking secret exec runs this command with ${plan.references.map(credentialName).join(", ")}. `
      + "Each value is put in place at run time through an environment variable and masked in the output; "
      + "the command itself holds only references.",
    updatedInput: { ...input, command: wrapCommand(command, wrapper) },
  });
}

/** What the model is told at session start while secret exec is on. */
export const SECRET_EXEC_USAGE = "OpenViking secret exec is on. When a shell command needs a saved credential, write its "
  + "reference ({{ov_privacy:<user>:viking://~/privacy/credential/<target>#/values/<field>}}, as privacy_tree lists it) "
  + "verbatim in the Bash command, quoted however the command needs. The plugin runs such a command through its "
  + "runner: the value goes into the command's environment when it runs, and any value the command prints comes "
  + "back as its reference. Do not call privacy_resolve, ask for the value, or try to print, encode or store it. "
  + "The command runs in its own shell, so cd and exported variables do not carry over, and a here-document that "
  + "holds a reference needs an unquoted delimiter (<<EOF).";

/**
 * Replace every secret value in a byte stream by its reference.
 *
 * Each value is matched as written and as it reads inside a JSON string,
 * leftmost first and longest first where two start together. Between chunks
 * only the longest tail that could still grow into a value is held back, so
 * output with no near-miss passes through at once.
 */
export function createMasker(secrets = []) {
  const needles = [];
  for (const { value, reference } of secrets) {
    for (const form of new Set([value, JSON.stringify(value).slice(1, -1)])) {
      if (form) needles.push({ bytes: Buffer.from(form), reference: Buffer.from(reference) });
    }
  }
  needles.sort((a, b) => b.bytes.length - a.bytes.length);

  // Where the longest suffix of data[from:] that some needle starts with begins.
  const heldFrom = (data, from) => {
    let keep = 0;
    for (const { bytes } of needles) {
      for (let size = Math.min(bytes.length - 1, data.length - from); size > keep; size -= 1) {
        if (data.compare(bytes, 0, size, data.length - size) === 0) {
          keep = size;
          break;
        }
      }
    }
    return data.length - keep;
  };

  const mask = (data, final) => {
    const parts = [];
    const found = needles.map(({ bytes }) => data.indexOf(bytes));
    let pos = 0;
    let hold = final ? data.length : heldFrom(data, 0);
    for (;;) {
      let best = -1;
      for (let k = 0; k < needles.length; k += 1) {
        if (found[k] >= 0 && found[k] < pos) found[k] = data.indexOf(needles[k].bytes, pos);
        if (found[k] >= 0 && (best < 0 || found[k] < found[best])) best = k;
      }
      if (best < 0 || found[best] >= hold) {
        parts.push(data.subarray(pos, hold));
        return { out: Buffer.concat(parts), rest: data.subarray(hold) };
      }
      parts.push(data.subarray(pos, found[best]), needles[best].reference);
      pos = found[best] + needles[best].bytes.length;
      if (pos > hold) hold = heldFrom(data, pos);
    }
  };

  let rest = Buffer.alloc(0);
  return {
    write(chunk) {
      const result = mask(rest.length ? Buffer.concat([rest, chunk]) : chunk, false);
      rest = result.rest;
      return result.out;
    },
    end() {
      const { out } = mask(rest, true);
      rest = Buffer.alloc(0);
      return out;
    },
  };
}

/** `resolve(reference)` for `runSecretExec`, over a plugin's `fetchJSON`. */
export function privacyResolver(fetchJSON) {
  return async (reference) => {
    const res = await fetchJSON("/api/v1/privacy-configs/resolve", {
      method: "POST",
      body: JSON.stringify({ selector: reference }),
    });
    const value = res.ok ? res.result?.value : undefined;
    if (typeof value === "string" && value) return { ok: true, value };
    const reason = res.ok ? "no value is stored" : res.error?.code || (res.status ? `HTTP ${res.status}` : "OpenViking is unreachable");
    return { ok: false, reason };
  };
}

const POSIX_SHELLS = new Set(["sh", "bash", "zsh", "dash", "ksh"]);

/** The user's shell when it is a POSIX one, else bash from PATH, else /bin/sh. */
export function posixShell(env = {}, exists = () => false) {
  const shell = String(env.SHELL || "");
  if (POSIX_SHELLS.has(basename(shell))) return shell;
  const bash = String(env.PATH || "").split(":").filter(Boolean).map((dir) => `${dir}/bash`).find(exists);
  return bash || "/bin/sh";
}

const FORWARDED_SIGNALS = ["SIGINT", "SIGTERM", "SIGHUP"];

function pipeMasked(source, sink, secrets) {
  const masker = createMasker(secrets);
  const masked = new Transform({
    transform(chunk, _encoding, done) { done(null, masker.write(chunk)); },
    flush(done) { done(null, masker.end()); },
  });
  source.on("error", () => masked.end());
  source.pipe(masked).pipe(sink, { end: false });
  return new Promise((done) => masked.once("close", done));
}

/**
 * The runner: resolve every reference in `command`, then run it through
 * `shell -c` with the values in its environment and its output masked.
 *
 * Nothing runs unless every reference resolves, and no error names more than
 * the reference. Returns the exit code to leave with: the command's own, or
 * 128 + the signal that ended it.
 */
export async function runSecretExec(command, { env, resolve, spawn, shell, stdout, stderr, signals }) {
  const refuse = (lines) => {
    for (const line of [lines].flat()) stderr.write(`OpenViking secret exec: ${line}\n`);
    return 1;
  };
  if (typeof command !== "string" || !command.trim()) return refuse("usage: secret-run '<command>'");
  // A runner inside a runner would receive the outer one's values as command text.
  if (Object.keys(env).some((name) => /^OV_SECRET_\d+$/.test(name))) {
    return refuse("refusing to run inside another secret-exec command");
  }
  const plan = rewriteCommand(command);
  if (plan.error) return refuse(plan.error);

  const answers = await Promise.all(plan.references.map((reference) => (
    Promise.resolve().then(() => resolve(reference)).catch(() => ({ ok: false }))
  )));
  const usable = (answer) => answer?.ok === true && typeof answer.value === "string" && answer.value !== "";
  const failed = plan.references.flatMap((reference, n) => (
    usable(answers[n]) ? [] : [`cannot resolve ${reference} (${answers[n]?.reason || "no value"}); nothing was run`]
  ));
  if (failed.length) return refuse(failed);

  const secrets = plan.references.map((reference, n) => ({ reference, value: answers[n].value }));
  const child = spawn(shell, ["-c", plan.script], {
    env: { ...env, ...Object.fromEntries(secrets.map(({ value }, n) => [`OV_SECRET_${n}`, value])) },
    stdio: ["inherit", "pipe", "pipe"],
  });
  const forward = (signal) => child.kill(signal);
  for (const signal of FORWARDED_SIGNALS) signals.on(signal, forward);
  const drained = Promise.all([pipeMasked(child.stdout, stdout, secrets), pipeMasked(child.stderr, stderr, secrets)]);
  const exit = await new Promise((done) => {
    child.once("error", (error) => done({ error }));
    child.once("close", (code, signal) => done({ code, signal }));
  });
  for (const signal of FORWARDED_SIGNALS) signals.off(signal, forward);
  await drained;
  if (exit.error) return refuse(`cannot start ${shell} (${exit.error.code || "spawn failed"})`);
  return exit.code ?? 128 + (constants.signals[exit.signal] || 0);
}
