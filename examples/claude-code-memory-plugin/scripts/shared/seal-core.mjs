// GENERATED FROM examples/agent-hook-plugin/lib. DO NOT EDIT.
/**
 * Plugin-side sealing: the part every harness adapter shares.
 *
 * A harness plugin keeps registered secrets out of what the model sees and
 * gives tools the real values. The OpenViking server does the matching
 * (SEAL_PATH; see "Seal Texts" in docs/en/api/10-privacy.md) and turns a
 * reference back into its value (RESOLVE_PATH); this module builds the
 * requests, checks the answers and walks JSON-like values. Each adapter
 * decides where to call it and what a failure does there. The exported API
 * is typed in seal-core.d.mts.
 *
 * Pure ES module: no Node built-ins, no DOM, no global `fetch`, so it loads in
 * Node and in the Claude Code mods runtime alike. All network access goes
 * through the caller's `post(path, body) -> Promise<{ status, json }>`, where
 * `json` is the server's `{ status: "ok", result }` envelope; `post` rejects
 * when no answer arrived (`name` AbortError or TimeoutError, or `aborted:
 * true`, reads as a timeout). Every failure is a SealError.
 *
 * Nothing here logs a text, a value or a response.
 */

export const SEAL_PATH = "/api/v1/privacy-configs/seal";
export const RESOLVE_PATH = "/api/v1/privacy-configs/resolve";

// The seal endpoint takes at most this many texts per call.
export const MAX_TEXTS = 1024;
// The endpoint allows 16 MiB of text per call; a request body through the
// Claude Code mods runtime may be 4 MiB, so a call carries about 3 MiB.
export const BATCH_BYTES = 3 * 1024 * 1024;
export const MAX_TEXT_BYTES = 16 * 1024 * 1024;
// Registered values are at least this long, so shorter strings cannot hold one.
export const MIN_LENGTH = 6;

// The full canonical reference, in `credential` or `auto`.
const REFERENCE_SOURCE =
  "\\{\\{ov_privacy:([^\\s:{}]+):viking:\\/\\/~\\/privacy\\/(?:credential|auto)\\/[^#\\s/{}]+#\\/values\\/[^{}\\s]+\\}\\}";
const CANONICAL_REFERENCE = new RegExp(`^${REFERENCE_SOURCE}$`);
// A tool argument holding this command hands references to the OpenViking CLI as they are.
const OV_PRIVACY = /(?<![\w-])ov\s+privacy\b/;

/**
 * Every reason sealing cannot be done; `reason` says which:
 * `seal_failed` (a call failed), `server_too_old` or `version_unknown`.
 */
export class SealError extends Error {
  constructor(message, { status, reason = "seal_failed" } = {}) {
    super(message);
    this.name = "SealError";
    this.reason = reason;
    if (status !== undefined) this.status = status;
  }
}

function malformed(what) {
  return new SealError(`malformed response: ${what}`);
}

export function isCanonicalReference(text) {
  return typeof text === "string" && CANONICAL_REFERENCE.test(text);
}

export function containsOvPrivacy(text) {
  return typeof text === "string" && OV_PRIVACY.test(text);
}

/** OpenViking's own tools understand references, so they get them as written. */
export function isOpenVikingTool(name, ownNames = []) {
  if (typeof name !== "string") return false;
  return name.toLowerCase().includes("openviking") || ownNames.includes(name);
}

/** What the model is told for the whole session while sealing is on. */
export const SESSION_TEXT =
  "Secrets are sealed: OpenViking replaces the secret values it knows with references, which start with `{{ov_privacy:` and end with `}}`, in what you read. " +
  "The replacement exists only in your view. A file on disk and the real output of a command still contain the secret itself, and a tool you call receives the real value in place of a reference you write. " +
  "So a reference seen in a file does not mean the file is free of secrets or safe to share, and a file you write through a tool holds the real value. " +
  "Write a reference exactly as it is wherever the secret is needed, such as in a tool call's arguments, and never ask for the value in plain text. " +
  "Read files and run commands normally: do not mask, cut or re-encode secrets yourself, because a partial or altered value is not recognised and is sent as it is. " +
  "Never print a secret or any part of it. " +
  "When the `ov` CLI is installed, `ov privacy run` runs a command with the references it is given; with `--env NAME=…`, use the variable inside `sh -c '…'`, because `$NAME` written directly on the command line is expanded by your own shell before `ov` runs. " +
  "A reference under `…/privacy/auto/…` was created automatically for a secret OpenViking found and works like any other reference. " +
  "Save one the user gave you and will use again under a proper name with a description, as the note that comes with it says; ones that only appeared in tool output or files need no action.";

/**
 * The note the model gets for a turn whose user text holds a temporary
 * reference.
 *
 * The temporary references fill the list and the upsert example. The run
 * example keeps `<reference>` as written: by then the model holds the
 * reference privacy_upsert returned, and the temporary one no longer resolves.
 */
export function sealNote(references) {
  const list = references.join(", ");
  const first = references[0];
  return (
    `OpenViking stored a secret from the user's message and replaced it with a reference: ${list}. ` +
    "You will not see the value. Unless it is clearly for one-off use, save it under a proper name now with privacy_upsert (or `ov privacy upsert`), " +
    "passing the reference as the value and a short description of what it is for, " +
    `for example category="credential", target_key="github", values={"token": "${first}"}, description="GitHub PAT for the acme org". ` +
    "The temporary entry is removed once it is saved, so use the reference that privacy_upsert returns from then on. " +
    "Write the reference as is wherever the secret is needed, for example " +
    "`ov privacy run --env NAME='<reference>' -- sh -c '<command using \"$NAME\">'`; " +
    "do not resolve it to plain text or print any part of it. " +
    "Tell the user what was stored and under which name."
  );
}

/** What the model sees in place of a tool result that could not be sealed. */
export function withheld(reason) {
  const why = reason ? ` (${reason})` : "";
  return (
    `[OpenViking withheld this tool result: sealing secrets in it failed${why}. ` +
    "The tool did run, but its output is not shown so that no secret reaches the model.]"
  );
}

// ── when sealing cannot be done ─────────────────────────────────────────

/** The SealError of a server without the capability, or null. */
function unsupported(status) {
  if (!status || status.supported === true) return null;
  const required = status.required || "a newer version";
  if (!status.actual) {
    return new SealError(
      `the OpenViking server's version could not be read (its health check failed), and sealing secrets needs ${required} or newer`,
      { reason: "version_unknown" },
    );
  }
  return new SealError(
    `the OpenViking server is version ${status.actual}, and sealing secrets needs ${required} or newer`,
    { reason: "server_too_old" },
  );
}

export function whyNotSealed(error) {
  const reason = error?.reason ?? "seal_failed";
  const message = error instanceof Error ? error.message : String(error ?? "unknown error");
  return reason === "seal_failed" ? `sealing secrets failed (${message})` : message;
}

/** What the user can do about `error`; the stop messages end with this. */
export function stopHint(error) {
  const fix =
    {
      server_too_old: "Upgrade the OpenViking server",
      version_unknown: "Make sure the OpenViking server is running and reachable",
      setting_unknown: "Use Claude Code where the plugin can run its setting helper (the CLI)",
    }[error?.reason] ?? "Try again once the OpenViking server answers";
  return (
    "With sealSecrets on, OpenViking sends nothing unsealed. " +
    `${fix}, or turn sealing off with sealSecrets: false (OPENVIKING_SEAL_SECRETS=0).`
  );
}

/**
 * Whether the server can seal. `check()` answers `{ supported, required,
 * actual }` for the server (`featureStatus("privacy_seal", version)`), or a
 * SealError of its own. `unavailable()` resolves the SealError of a server
 * that cannot seal (`server_too_old`, or `version_unknown` when the version
 * could not be read) or null. Only "can seal" is kept: a no is asked again the
 * next time, so sealing resumes once the server can. `guard(post)` is a `post`
 * that asks `unavailable()` first and makes no call while it says no, so a
 * pass that sends nothing does not ask the server at all.
 */
export function createSealPolicy({ check = async () => ({ supported: true }) } = {}) {
  let known = null;
  const policy = {
    unavailable() {
      const asked = (known ??= Promise.resolve()
        .then(check)
        .then(
          (status) => (status instanceof SealError ? status : unsupported(status)),
          () => unsupported({ supported: false }),
        ));
      asked.then((problem) => {
        if (problem && known === asked) known = null;
      });
      return asked;
    },
    guard(post) {
      return async (path, body) => {
        const problem = await policy.unavailable();
        if (problem) throw problem;
        return post(path, body);
      };
    },
  };
  return policy;
}

// ── sizes and code points ───────────────────────────────────────────────

function codePointLength(text) {
  let n = 0;
  for (const _ of text) n += 1;
  return n;
}

function utf8Size(text) {
  let n = 0;
  for (const ch of text) {
    const c = ch.codePointAt(0);
    n += c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
  }
  return n;
}

// ── talking to the server ───────────────────────────────────────────────

async function call(post, path, body) {
  try {
    return await post(path, body);
  } catch (error) {
    if (error instanceof SealError) throw error;
    if (error?.name === "AbortError" || error?.name === "TimeoutError" || error?.aborted) {
      throw new SealError("the OpenViking server did not answer in time");
    }
    const message = error instanceof Error ? error.message : String(error ?? "");
    throw new SealError(`cannot reach the OpenViking server${message ? `: ${message}` : ""}`);
  }
}

function httpFailure(status, json, missing) {
  if (status === 404 && missing) return new SealError(`HTTP 404: ${missing}`, { status });
  const message = json && typeof json === "object" ? json.error?.message : undefined;
  return new SealError(`HTTP ${status}${typeof message === "string" && message ? `: ${message}` : ""}`, { status });
}

/** The `result` of an ok answer; throws on anything else. */
function okResult(answer, missing) {
  if (!answer || typeof answer !== "object") throw malformed("no answer");
  const { status, json } = answer;
  if (!Number.isInteger(status)) throw malformed("no HTTP status");
  if (status < 200 || status >= 300) throw httpFailure(status, json, missing);
  if (!json || typeof json !== "object" || json.status !== "ok") throw malformed("the answer is not ok");
  return json.result;
}

// ── sealing ─────────────────────────────────────────────────────────────

/**
 * Check one `result.texts` item against its original text: offsets in range
 * and in order, and a canonical reference on every span. The sealed text is
 * built from the spans; the item's `text` is not read. An item with no spans
 * is no change.
 */
function checkItem(item, original, index) {
  const where = `texts[${index}]`;
  if (item === null) return null;
  if (typeof item !== "object" || Array.isArray(item)) throw malformed(`${where} is not an object or null`);
  if (!Array.isArray(item.replaced)) throw malformed(`${where}.replaced is not a list`);
  if (!item.replaced.length) return null;

  const chars = Array.from(original);
  const length = chars.length;
  const spans = [];
  const parts = [];
  let cursor = 0;
  for (const span of item.replaced) {
    if (!span || typeof span !== "object") throw malformed("a replacement is not an object");
    const { start, end, reference } = span;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < cursor || end <= start || end > length) {
      throw malformed("replacement offsets are out of order or out of range");
    }
    if (!isCanonicalReference(reference)) throw malformed("a replacement has no canonical reference");
    parts.push(chars.slice(cursor, start).join(""), reference);
    cursor = end;
    spans.push({ start, end, reference, temporary: span.temporary === true });
  }
  parts.push(chars.slice(cursor).join(""));
  return { text: parts.join(""), spans };
}

async function sealBatch(post, batch) {
  const answer = await call(post, SEAL_PATH, { texts: batch.map(({ text, enroll }) => ({ text, enroll })) });
  const result = okResult(answer, "the OpenViking server has no seal endpoint; it may be too old");
  if (!result || typeof result !== "object") throw malformed("no result");
  const { texts } = result;
  if (!Array.isArray(texts) || texts.length !== batch.length) throw malformed("result.texts does not match the request");
  return texts.map((item, i) => checkItem(item, batch[i].text, i));
}

function batches(units) {
  const out = [];
  let batch = [];
  let size = 0;
  for (const unit of units) {
    // The JSON-encoded size, which is what the request body carries.
    const length = utf8Size(JSON.stringify(unit.text)) + 32;
    if (batch.length && (batch.length === MAX_TEXTS || size + length > BATCH_BYTES)) {
      out.push(batch);
      batch = [];
      size = 0;
    }
    batch.push(unit);
    size += length;
  }
  if (batch.length) out.push(batch);
  return out;
}

function distinctTemporary(spans, into = []) {
  for (const span of spans) {
    if (span.temporary && !into.includes(span.reference)) into.push(span.reference);
  }
  return into;
}

/**
 * Seal `items` (`[{ text, enroll? }]`) and resolve `{ texts, temporary }`:
 * `texts[i]` is `{ text, changed, temporary, spans }` for `items[i]`, where
 * `text` is the text to use (the original when nothing was replaced),
 * `temporary` its distinct temporary references in order of first
 * appearance, and `spans` the checked replacements `{ start, end, reference,
 * temporary }` (code point offsets into the original). The top-level
 * `temporary` is the same over all texts, in input order. A duplicate text is
 * sent once; the request is split at MAX_TEXTS texts or about BATCH_BYTES per
 * call.
 */
export async function sealTexts(post, items) {
  if (!Array.isArray(items)) throw new SealError("sealTexts needs a list of texts");
  const units = [];
  const unitOf = new Map();
  const order = items.map((item) => {
    const text = item?.text;
    if (typeof text !== "string") throw new SealError("sealTexts needs a string text in every item");
    const enroll = item.enroll === true;
    if (utf8Size(text) > MAX_TEXT_BYTES) throw new SealError("a text is larger than the seal endpoint accepts");
    const key = `${enroll ? 1 : 0}\u0000${text}`;
    if (!unitOf.has(key)) {
      unitOf.set(key, units.length);
      units.push({ text, enroll });
    }
    return unitOf.get(key);
  });
  if (!units.length) return { texts: [], temporary: [] };

  const results = [];
  for (const batch of batches(units)) results.push(...(await sealBatch(post, batch)));

  const temporary = [];
  const texts = order.map((index) => {
    const item = results[index];
    if (!item) return { text: units[index].text, changed: false, temporary: [], spans: [] };
    distinctTemporary(item.spans, temporary);
    return { text: item.text, changed: true, temporary: distinctTemporary(item.spans), spans: item.spans };
  });
  return { texts, temporary };
}

// ── walking JSON-like values ────────────────────────────────────────────

function isPlainObject(node) {
  if (!node || typeof node !== "object") return false;
  const proto = Object.getPrototypeOf(node);
  return proto === Object.prototype || proto === null;
}

function eachString(node, visit, key) {
  if (typeof node === "string") {
    visit(node, key);
  } else if (Array.isArray(node)) {
    for (const item of node) eachString(item, visit);
  } else if (isPlainObject(node)) {
    for (const name of Object.keys(node)) eachString(node[name], visit, name);
  }
}

/** `node` with `edit(text, key)` applied to every string leaf; the same object where nothing changed. */
function mapStrings(node, edit, key) {
  if (typeof node === "string") return edit(node, key);
  if (Array.isArray(node)) {
    const items = node.map((item) => mapStrings(item, edit));
    return items.every((item, i) => item === node[i]) ? node : items;
  }
  if (isPlainObject(node)) {
    let out = null;
    for (const key of Object.keys(node)) {
      const next = mapStrings(node[key], edit, key);
      if (next !== node[key]) {
        out ??= Object.assign(Object.getPrototypeOf(node) === null ? Object.create(null) : {}, node);
        out[key] = next;
      }
    }
    return out ?? node;
  }
  return node;
}

// File content such as an image is not text a secret is typed into, and can
// be megabytes: a data: URI anywhere, and a long base64 run under the keys
// file content goes in (`data`, `file_data`), the gateway's rule. A long run
// under any other key may be a secret itself (a session token) and is sealed.
const BASE64 = /^[A-Za-z0-9+/=_-]{256,}$/;

function isEncodedData(text, key) {
  if (text.startsWith("data:") && text.slice(0, 256).includes(",")) return true;
  return (key === "data" || key === "file_data") && BASE64.test(text);
}

function isSealableLeaf(text, key) {
  return codePointLength(text) >= MIN_LENGTH && !isEncodedData(text, key);
}

/**
 * Seal every string leaf of a JSON-like value (arrays and plain objects;
 * object keys are never touched), all leaves in one sealTexts, and resolve
 * `{ value, temporary, sealed }`. Strings of fewer than MIN_LENGTH code points
 * are left as they are, and so is encoded file content (isEncodedData). Parts
 * with nothing replaced keep their identity; nothing replaced at
 * all returns the input itself. `sealed` counts the string leaves rewritten.
 */
export async function sealValue(post, value, { enroll = false } = {}) {
  const leaves = [];
  eachString(value, (text, key) => {
    if (isSealableLeaf(text, key)) leaves.push(text);
  });
  const unique = [...new Set(leaves)];
  if (!unique.length) return { value, temporary: [], sealed: 0 };

  const { texts } = await sealTexts(post, unique.map((text) => ({ text, enroll: enroll === true })));
  const byText = new Map(unique.map((text, i) => [text, texts[i]]));
  const temporary = [];
  for (const text of leaves) distinctTemporary(byText.get(text).spans, temporary);
  let sealed = 0;
  const rewritten = mapStrings(value, (text, key) => {
    const result = isSealableLeaf(text, key) && byText.get(text);
    if (!result?.changed) return text;
    sealed += 1;
    return result.text;
  });
  return { value: rewritten, temporary, sealed };
}

// ── unsealing ───────────────────────────────────────────────────────────

function referencesIn(text) {
  if (containsOvPrivacy(text)) return [];
  return [...text.matchAll(new RegExp(REFERENCE_SOURCE, "g"))];
}

// 404 (unknown) and 403 (another user's reference) leave the reference as it is.
async function resolveOne(post, reference) {
  let result;
  try {
    const answer = await call(post, RESOLVE_PATH, { selector: reference });
    if (answer && typeof answer === "object" && (answer.status === 404 || answer.status === 403)) {
      return null;
    }
    result = okResult(answer);
  } catch (error) {
    if (error instanceof SealError && error.reason !== "seal_failed") throw error;
    throw new SealError(`could not resolve a secret reference: ${error.message}`, { status: error.status });
  }
  const value = result && typeof result === "object" ? result.value : undefined;
  return typeof value === "string" && value ? value : null;
}

/**
 * Every whole canonical reference in a string leaf is replaced
 * by its value once the resolve endpoint returned one and sealing that value
 * alone gives back exactly one span over all of it with the same reference;
 * resolve `{ value, replaced }`. A string that contains an `ov privacy`
 * invocation is left untouched as a whole. `skip: true` (the arguments of an
 * OpenViking or agent-spawning tool) returns the value as it is without a
 * request. Each reference is resolved at most once per call; `replaced`
 * counts the occurrences rewritten.
 */
export async function unsealValue(post, value, { skip = false } = {}) {
  if (skip) return { value, replaced: 0 };
  const found = new Set();
  eachString(value, (text) => {
    for (const match of referencesIn(text)) found.add(match[0]);
  });
  if (!found.size) return { value, replaced: 0 };

  const references = [...found];
  const resolved = new Map();
  for (const reference of references) resolved.set(reference, await resolveOne(post, reference));

  // Only a value that seals back to its own reference: the next turn must
  // show the model the reference it wrote.
  const candidates = [...new Set([...resolved.values()].filter(Boolean))];
  const values = new Map();
  if (candidates.length) {
    let checked;
    try {
      ({ texts: checked } = await sealTexts(post, candidates.map((text) => ({ text, enroll: false }))));
    } catch (error) {
      if (error instanceof SealError && error.reason !== "seal_failed") throw error;
      throw new SealError(`could not confirm a secret reference: ${error.message}`, { status: error.status });
    }
    const byValue = new Map(candidates.map((text, i) => [text, checked[i]]));
    for (const [reference, plain] of resolved) {
      if (!plain) continue;
      const { spans } = byValue.get(plain);
      const exact =
        spans.length === 1 &&
        spans[0].start === 0 &&
        spans[0].end === codePointLength(plain) &&
        spans[0].reference === reference;
      if (exact) values.set(reference, plain);
    }
  }
  if (!values.size) return { value, replaced: 0 };

  let replaced = 0;
  const rewritten = mapStrings(value, (text) => {
    const matches = referencesIn(text).filter((match) => values.has(match[0]));
    if (!matches.length) return text;
    let out = "";
    let cursor = 0;
    for (const match of matches) {
      out += text.slice(cursor, match.index) + values.get(match[0]);
      cursor = match.index + match[0].length;
    }
    replaced += matches.length;
    return out + text.slice(cursor);
  });
  return { value: rewritten, replaced };
}
