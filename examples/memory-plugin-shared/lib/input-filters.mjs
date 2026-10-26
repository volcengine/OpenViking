/**
 * Ordered, operator-configured regex filters for memory plugin input.
 *
 * Rules are sed-style strings, applied in order to a single piece of text:
 *   s/^\s*ultrathink\s+//i   substitute (add `g` to replace every match)
 *   d|^\s*[/!]|              drop the text when the pattern matches
 *   k/^\?ov\b/               keep the text only when the pattern matches
 *   user:d/^\s*\/clear\b/    apply to one role only
 *
 * A malformed rule becomes an entry in `errors` and is skipped rather than
 * throwing, so a typo in a config file cannot take a hook down.
 */

const OPS = new Set(["s", "d", "k"]);
const SCOPES = ["user", "assistant"];
const ALLOWED_FLAGS = "imsug";

export const INPUT_FILTER_KNOBS = [
  {
    key: "recallQueryFilters",
    env: "OPENVIKING_RECALL_QUERY_FILTERS",
    label: "recall query filters",
  },
  {
    key: "captureFilters",
    env: "OPENVIKING_CAPTURE_FILTERS",
    label: "capture filters",
  },
];

/**
 * Read one delimited field. `\` always consumes the next character, and only
 * `\<delim>` is un-escaped, so `\d` / `\b` / `\x2c` reach RegExp verbatim.
 * Returns the index of the closing delimiter, or -1 when it is missing.
 */
function scanField(source, start, delim) {
  let out = "";
  let i = start;
  while (i < source.length) {
    const c = source[i];
    if (c === "\\") {
      const next = source[i + 1];
      if (next === undefined) {
        out += "\\";
        i += 1;
        continue;
      }
      out += next === delim ? next : "\\" + next;
      i += 2;
      continue;
    }
    if (c === delim) return { value: out, end: i };
    out += c;
    i += 1;
  }
  return { value: out, end: -1 };
}

export function parseInputFilterRule(source) {
  if (typeof source !== "string") {
    return { source: String(source ?? ""), error: "rule must be a string" };
  }
  const raw = source.trim();
  if (!raw) return { source: raw, error: "rule is empty" };

  let scope = "";
  let rest = raw;
  for (const candidate of SCOPES) {
    if (rest.startsWith(`${candidate}:`)) {
      scope = candidate;
      rest = rest.slice(candidate.length + 1);
      break;
    }
  }

  const op = rest[0] || "";
  if (!OPS.has(op)) {
    return {
      source: raw,
      error: `unknown operation "${op}" — expected s (substitute), d (drop) or k (keep)`,
    };
  }
  const delim = rest[1];
  if (delim === undefined) {
    return { source: raw, error: `missing delimiter after "${op}"` };
  }
  if (/[A-Za-z0-9\s\\]/.test(delim)) {
    return {
      source: raw,
      error: `invalid delimiter ${JSON.stringify(delim)} — use punctuation such as / | # or :`,
    };
  }

  const first = scanField(rest, 2, delim);
  const pattern = first.value;
  let replacement = null;
  let flagsRaw = "";
  if (op === "s") {
    if (first.end < 0) {
      return { source: raw, error: `missing ${JSON.stringify(delim)} after the pattern` };
    }
    const second = scanField(rest, first.end + 1, delim);
    if (second.end < 0) {
      return {
        source: raw,
        error: `missing ${JSON.stringify(delim)} after the replacement — write s${delim}pattern${delim}replacement${delim}`,
      };
    }
    replacement = second.value;
    flagsRaw = rest.slice(second.end + 1);
  } else {
    flagsRaw = first.end < 0 ? "" : rest.slice(first.end + 1);
  }

  if (!pattern) return { source: raw, error: "pattern is empty" };

  const flags = new Set();
  for (const flag of flagsRaw) {
    if (!ALLOWED_FLAGS.includes(flag)) {
      return { source: raw, error: `unknown flag "${flag}" — allowed flags are i, m, s, u, g` };
    }
    if (flags.has(flag)) return { source: raw, error: `duplicate flag "${flag}"` };
    flags.add(flag);
  }
  // d/k decide with .test(), which advances lastIndex on a global regex and
  // would then alternate between calls. `g` means nothing there, so drop it.
  if (op !== "s") flags.delete("g");

  let re;
  try {
    re = new RegExp(pattern, [...flags].join(""));
  } catch (err) {
    // V8 already says "Invalid regular expression: <pattern>: <why>"; keep that
    // detail without stuttering the prefix the doctors key off.
    const message = err?.message || String(err);
    return {
      source: raw,
      error: /^invalid regular expression/i.test(message)
        ? `invalid${message.slice("Invalid".length)}`
        : `invalid regular expression: ${message}`,
    };
  }

  return { op, scope, re, replacement, source: raw };
}

export function compileInputFilters(rules) {
  const compiled = [];
  const errors = [];
  const list = Array.isArray(rules) ? rules : [];
  list.forEach((entry, index) => {
    if (typeof entry !== "string") {
      errors.push({ index, source: String(entry ?? ""), message: "rule must be a string" });
      return;
    }
    if (!entry.trim()) return;
    const parsed = parseInputFilterRule(entry);
    if (parsed.error) errors.push({ index, source: parsed.source, message: parsed.error });
    else compiled.push({ ...parsed, index });
  });
  return { rules: compiled, errors };
}

/**
 * Run compiled rules over `text`.
 *
 * `substituteOnly` skips the d/k operations, so a caller that already took one
 * drop decision for a turn can rewrite its individual pieces without taking a
 * second, possibly contradictory one.
 */
export function applyInputFilters(text, compiled, { role = "", substituteOnly = false } = {}) {
  const original = typeof text === "string" ? text : String(text ?? "");
  let current = original;
  for (const rule of Array.isArray(compiled) ? compiled : []) {
    if (rule.scope && rule.scope !== role) continue;
    if (rule.op === "s") {
      current = current.replace(rule.re, rule.replacement);
      continue;
    }
    if (substituteOnly) continue;
    const matched = rule.re.test(current);
    if ((rule.op === "d" && matched) || (rule.op === "k" && !matched)) {
      return { text: "", changed: false, dropped: true, ruleIndex: rule.index, op: rule.op };
    }
  }
  const finalText = current.trim();
  return {
    text: finalText,
    changed: finalText !== original,
    dropped: false,
    ruleIndex: -1,
    op: "",
  };
}

/** Doctor-facing summary of both filter knobs on a resolved plugin config. */
export function describeInputFilters(cfg = {}) {
  return INPUT_FILTER_KNOBS.map(({ key, env, label }) => {
    const configured = Array.isArray(cfg?.[key]) ? cfg[key] : [];
    const compiled = compileInputFilters(configured);
    const ops = { s: 0, d: 0, k: 0 };
    for (const rule of compiled.rules) ops[rule.op] += 1;
    const bits = [];
    if (ops.s) bits.push(`${ops.s} substitute`);
    if (ops.d) bits.push(`${ops.d} drop`);
    if (ops.k) bits.push(`${ops.k} keep-only`);
    const active = compiled.rules.length;
    return {
      key,
      env,
      label,
      total: configured.length,
      active,
      summary: `${active} rule${active === 1 ? "" : "s"}${bits.length ? ` (${bits.join(", ")})` : ""}`,
      errors: compiled.errors,
    };
  });
}
