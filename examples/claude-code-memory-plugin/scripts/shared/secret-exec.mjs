// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Secret exec: a shell command names a saved credential by its reference, and
 * the value exists only in the environment of the process that runs it.
 *
 *   model writes  curl -H "Authorization: Bearer {{ov_privacy:...}}" https://...
 *   hook rewrites false && curl -H "Authorization: Bearer {{ov_privacy:...}}" https://...
 *                 <node> <runner> 'curl -H "Authorization: Bearer {{ov_privacy:...}}" https://...'
 *   runner        resolves each reference, runs the command with ${OV_SECRET_0}
 *                 where the reference stood, and masks the values in the output
 *                 back to their references
 *
 * The first line never runs. It shows the command to the host's permission
 * rules, which would otherwise see only the runner and a quoted string.
 *
 * The hook half never touches the network and never sees a value, so a hook
 * failure leaves the references in place and the command fails rather than
 * leaks. The runner half takes every effect as an argument.
 */

import { constants } from "node:os";
import { basename, delimiter, join } from "node:path";
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
// scanner misreads still costs something: a literal `${OV_SECRET_n}`, or an
// expansion left unquoted, which splits a value holding blanks into a form the
// masker does not know.
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
 * here-documents, arithmetic and, best effort, `$( )` and backticks.
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
  // `plain` is unquoted shell: the top level, or the inside of `$( )`, `$(( ))`
  // or backticks. A `heredoc` frame is a here-document body.
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
    if (frame.kind === "heredoc" && command[i - 1] === "\n") {
      // A body line: the delimiter ends the here-document, and under a quoted
      // delimiter the line is literal.
      const end = command.indexOf("\n", i) + 1 || command.length;
      const line = command.slice(i, end);
      if ((frame.strip ? line.replace(/^\t+/, "") : line).replace(/\n$/, "") === frame.word) {
        stack.pop();
        take(end - i);
        continue;
      }
      if (!frame.expand) {
        if (REFERENCE.test(line)) return { error: QUOTED_HEREDOC };
        take(end - i);
        continue;
      }
    }
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
    } else if (command.startsWith("$((", i) || (frame.kind === "plain" && command.startsWith("((", i))) {
      // Arithmetic, where `<<` is a shift and not a here-document.
      stack.push({ kind: "plain", close: ")", depth: 1, arith: true });
      take(c === "$" ? 3 : 2);
    } else if (command.startsWith("$(", i)) {
      stack.push({ kind: "plain", close: ")", depth: 0 });
      take(2);
    } else if (c === "`") {
      if (frame.close === "`") stack.pop();
      else stack.push({ kind: "plain", close: "`" });
      take(1);
    } else if (frame.kind !== "plain") {
      // In double quotes or a here-document body nothing else is syntax, but
      // a double quote may end the string.
      if (c === '"' && frame.kind === "double") stack.pop();
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
    } else if (command.startsWith("<<", i) && !frame.arith) {
      const { end, doc } = readHeredoc(command, i + 2);
      if (doc) heredocs.push({ kind: "heredoc", ...doc });
      take(end - i);
    } else if (c === "\n" && heredocs.length) {
      take(1);
      // The bodies follow in the order of their operators, so the first goes on top.
      stack.push(...heredocs.splice(0).reverse());
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

// A path the shell reads as one word as it stands.
const PLAIN_WORD = /^[\w@%+=:,./-]+$/;

/**
 * `<node> <runner> '<command>'`, the command intact so a permission prompt
 * shows what will run. The two paths go unquoted where they can, so a
 * permission rule for the runner is the line's own first two words.
 */
export function wrapCommand(command, { node, runner }) {
  const word = (path) => (PLAIN_WORD.test(path) ? path : shellQuote(path));
  return `${word(node)} ${word(runner)} ${shellQuote(command)}`;
}

// A word that opens or closes a compound command.
const COMPOUND = /(?:if|then|elif|else|fi|for|select|while|until|do|done|case|esac|function|time|coproc|!|\{|\}|\[\[)(?=[\s;&|()<>]|$)/y;
// A parameter expansion with nothing in it that could hold a quote or a command.
const PARAMETER = /\$\{[\w:+=?.,@/%#^*!\[\]-]*\}/y;

/**
 * The pipelines `command` runs one after another, or null when it is more than
 * a plain list of them: a here-document, `$( )`, backticks, a subshell or a
 * compound command are not read, because a separator missed in one would let
 * part of a guard line run.
 */
export function splitCommands(command) {
  const reference = new RegExp(REFERENCE.source, "y");
  const at = (pattern, index) => {
    pattern.lastIndex = index;
    return pattern.exec(command)?.[0];
  };
  const parts = [];
  let part = "";
  let quote = "";
  // Whether the next word is a command name: at the start, and after each `|`.
  let head = true;
  let i = 0;
  const take = (n) => {
    part += command.slice(i, i + n);
    i += n;
  };
  const cut = (n) => {
    if (part.trim()) parts.push(part.trim());
    part = "";
    head = true;
    i += n;
  };

  while (i < command.length) {
    const c = command[i];
    const next = command[i + 1];
    if (head && /\S/.test(c)) {
      if (at(COMPOUND, i)) return null;
      head = false;
    }
    const whole = quote !== "'" && (at(reference, i) || at(PARAMETER, i));
    if (quote === "'") {
      if (c === "'") quote = "";
      take(1);
    } else if (whole) {
      take(whole.length);
    } else if (c === "\\") {
      take(2);
    } else if (c === "`" || (c === "$" && "({['".includes(next ?? " "))) {
      return null;
    } else if (quote) {
      if (c === '"') quote = "";
      take(1);
    } else if (c === "'" || c === '"') {
      quote = c;
      take(1);
    } else if (c === "(" || c === ")") {
      return null;
    } else if (c === "<" && next === "<") {
      if (command[i + 2] !== "<") return null;
      take(3);
    } else if (c === "#" && (i === 0 || /[\s;&|]/.test(command[i - 1]))) {
      const eol = command.indexOf("\n", i);
      i = eol < 0 ? command.length : eol;
    } else if (c === ";") {
      cut(1);
    } else if (c === "\n") {
      // A pipeline goes on past a line that ends in its `|`.
      if (/\|&?$/.test(part.trimEnd())) take(1);
      else cut(1);
    } else if (c === "&") {
      if (next === "&") cut(2);
      // `&>`, `>&` and `<&` are redirections.
      else if (next === ">" || /[<>]$/.test(part)) take(1);
      else cut(1);
    } else if (c === "|") {
      if (next === "|") {
        cut(2);
      } else {
        take(next === "&" ? 2 : 1);
        head = true;
      }
    } else {
      take(1);
    }
  }
  if (quote) return null;
  cut(0);
  // A trailing backslash would join a guard line to the line after it.
  return parts.some((text) => text.endsWith("\\")) ? null : parts;
}

/**
 * The wrapper for `command` behind one guard line per pipeline in it, or null
 * when `splitCommands` cannot tell them apart.
 *
 * `false && <pipeline>` never runs the pipeline, but a permission check that
 * goes by the text of a command reads it like any other. So the guarded
 * command needs what `command` itself needs, plus the runner: a deny rule
 * still denies, and allowing the runner allows nothing else.
 */
export function guardCommand(command, wrapper) {
  const commands = splitCommands(command);
  return commands && [...commands.map((text) => `false && ${text}`), wrapCommand(command, wrapper)].join("\n");
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

const RUNS_RUNNER = "OpenViking secret exec: this command starts the secret-exec runner itself, "
  + "which puts the value of every credential reference that reaches it into the command it runs. "
  + "Your permission rules cannot see what it will run, so it is yours to confirm.";

const UNGUARDED = " This command is more than a list of plain commands, so your permission rules "
  + "cannot be checked against what it runs; it is yours to confirm.";

const preToolUse = (fields) => ({ hookSpecificOutput: { hookEventName: "PreToolUse", ...fields } });

/**
 * The PreToolUse answer for one tool call, or null to leave it alone.
 *
 * A Bash command that names a credential is wrapped in the runner behind its
 * guard lines, with every other input field kept. No decision is given, so the
 * user's own rules decide, as they would for the command itself. The user is
 * asked instead when `secretExecApproval` is `ask`, or when the rules could
 * not see the command: it cannot be guarded, or it starts the runner itself.
 * It is never `allow`: a reference would then be a way past approval for any
 * command. `privacy_resolve` is denied: it would put the value in the
 * conversation.
 */
export function secretExecPreToolUse(event = {}, cfg = {}, wrapper = {}) {
  if (!cfg.secretExec) return null;
  const toolName = String(event.tool_name ?? "");
  if (RESOLVE_TOOL.test(toolName)) {
    return preToolUse({ permissionDecision: "deny", permissionDecisionReason: RESOLVE_DENIED });
  }
  const input = event.tool_input;
  const command = input?.command;
  if (toolName !== "Bash" || typeof command !== "string") return null;
  // The hook sees only what the model wrote, so a command that names a runner,
  // from any install since an update moves the plugin root, starts it by hand.
  // It is not wrapped again: the inner runner would get the outer one's values
  // as command text. It is confirmed, because the runner resolves whatever
  // reference reaches it and the rules that allow the runner cannot see inside.
  const runner = basename(String(wrapper.runner ?? ""));
  if (runner && command.includes(runner)) {
    return preToolUse({ permissionDecision: "ask", permissionDecisionReason: RUNS_RUNNER });
  }
  if (!hasReference(command)) return null;

  const plan = rewriteCommand(command);
  if (plan.error) {
    return preToolUse({ permissionDecision: "deny", permissionDecisionReason: `OpenViking secret exec: ${plan.error}` });
  }
  if (!plan.references.length) return null;
  const guarded = guardCommand(command, wrapper);
  return preToolUse({
    ...((cfg.secretExecApproval === "ask" || !guarded) && { permissionDecision: "ask" }),
    permissionDecisionReason: `OpenViking secret exec runs this command with ${plan.references.map(credentialName).join(", ")}. `
      + "Each value is put in place at run time through an environment variable and masked in the output; "
      + `the command itself holds only references.${guarded ? "" : UNGUARDED}`,
    updatedInput: { ...input, command: guarded ?? wrapCommand(command, wrapper) },
  });
}

/** What the model is told at session start while secret exec is on. */
export const SECRET_EXEC_USAGE = "OpenViking secret exec is on. When a shell command needs a saved credential, write its "
  + "reference ({{ov_privacy:<user>:viking://~/privacy/credential/<target>#/values/<field>}}, as privacy_tree lists it) "
  + "verbatim in the Bash command, quoted however the command needs. The plugin runs such a command through its "
  + "runner: the value goes into the command's environment when it runs, and any value the command prints comes "
  + "back as its reference. Do not call privacy_resolve, ask for the value, or try to print, encode or store it. "
  + "The command runs in its own shell, so cd and exported variables do not carry over, and a here-document that "
  + "holds a reference needs an unquoted delimiter (<<EOF). Keep such a command to plain commands and pipelines "
  + "where you can: with $( ), a here-document, a subshell or if/for in it, the user is asked to confirm it each time.";

/**
 * Replace every secret value in a byte stream by its reference.
 *
 * Each value is matched as written and as it reads inside a JSON string,
 * leftmost first and longest first where two start together. A value saved
 * under several fields becomes the smallest of their references, as the
 * Gateway seals it. Between chunks only the longest tail that could still grow
 * into a value is held back, so output with no near-miss passes through at once.
 */
export function createMasker(secrets = []) {
  const needles = [];
  for (const { value, reference } of secrets) {
    for (const form of new Set([value, JSON.stringify(value).slice(1, -1)])) {
      if (form) needles.push({ bytes: Buffer.from(form), reference: Buffer.from(reference) });
    }
  }
  needles.sort((a, b) => b.bytes.length - a.bytes.length || a.reference.compare(b.reference));

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
    if (!res.ok) {
      return { ok: false, reason: res.error?.code || (res.status ? `HTTP ${res.status}` : "OpenViking is unreachable") };
    }
    const value = res.result?.value;
    // An environment variable holds text, and no NUL byte.
    if (typeof value === "string" && value && !value.includes("\0")) return { ok: true, value };
    return { ok: false, reason: value == null || value === "" ? "no value is stored" : "the stored value is not text" };
  };
}

/**
 * The shell Claude Code's Bash tool would pick, so a command behaves the same
 * wrapped: CLAUDE_CODE_SHELL or SHELL when it is bash or zsh, else zsh or bash
 * from PATH, and /bin/sh only when neither is installed.
 */
export function posixShell(env = {}, exists = () => false) {
  const preferred = [env.CLAUDE_CODE_SHELL, env.SHELL]
    .find((shell) => /^(bash|zsh)$/.test(basename(String(shell || ""))) && exists(shell));
  const onPath = (name) => String(env.PATH || "").split(delimiter).filter(Boolean).map((dir) => join(dir, name)).find(exists);
  return preferred || onPath("zsh") || onPath("bash") || "/bin/sh";
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
  const cannotStart = (error) => refuse(`cannot start ${shell} (${error.code || "spawn failed"})`);
  let child;
  try {
    child = spawn(shell, ["-c", plan.script], {
      env: { ...env, ...Object.fromEntries(secrets.map(({ value }, n) => [`OV_SECRET_${n}`, value])) },
      stdio: ["inherit", "pipe", "pipe"],
    });
  } catch (error) {
    // Its message quotes the environment entry it rejected, value included.
    return cannotStart(error);
  }
  const forward = (signal) => child.kill(signal);
  for (const signal of FORWARDED_SIGNALS) signals.on(signal, forward);
  const drained = Promise.all([pipeMasked(child.stdout, stdout, secrets), pipeMasked(child.stderr, stderr, secrets)]);
  const exit = await new Promise((done) => {
    child.once("error", (error) => done({ error }));
    child.once("close", (code, signal) => done({ code, signal }));
  });
  for (const signal of FORWARDED_SIGNALS) signals.off(signal, forward);
  await drained;
  if (exit.error) return cannotStart(exit.error);
  return exit.code ?? 128 + (constants.signals[exit.signal] || 0);
}
