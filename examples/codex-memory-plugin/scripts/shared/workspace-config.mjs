// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Layered workspace configuration.
 *
 * A workspace may carry two files under `<root>/.openviking/`: `config.json`,
 * which the team commits, and `config.local.json`, which stays private. A third
 * layer lives per machine in the user registry. All three share this schema and
 * this merge, so there is one set of rules to learn and one to test.
 *
 * These files are trusted without a prompt — a hook is non-interactive, and any
 * approval gate degrades into "run one command per workspace first". What is
 * refused instead is structural, and costs nobody anything:
 *
 *   - connection and credential keys are stripped, loudly. "Which server does
 *     my data go to" must stay answerable without reading a repository.
 *   - `${VAR}` is never expanded here. Variable expansion is a property of the
 *     layer, not of the parser: a committed file that could expand `${HOME}`
 *     could exfiltrate the environment.
 *
 * Everything else a workspace file sets takes effect. What it turns off is
 * announced rather than blocked.
 */

import { readFileSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, join, relative } from "node:path";

import { CONFIG_DIR_NAME, LOCAL_FILE, TEAM_FILE } from "./workspace-identity.mjs";

/**
 * `JSON.parse` keeps `__proto__` as an own property, and assigning it walks
 * into `Object.prototype`. Since `process.env` reads through the prototype
 * chain and the environment outranks ovcli.conf, one such key in a committed
 * file would set `OPENVIKING_URL` and `OPENVIKING_API_KEY` for the whole
 * process — the exact thing every other rule here exists to prevent.
 */
const UNSAFE_KEYS = ["__proto__", "constructor", "prototype"];

// Deep enough for any real config; a file can nest far past the stack limit
// inside the 64 KiB cap, and an overflow here would take out sibling layers.
const MAX_DEPTH = 32;

export { CONFIG_DIR_NAME, TEAM_FILE, LOCAL_FILE };
export const CONFIG_VERSION = 1;
export const MAX_CONFIG_BYTES = 64 * 1024;

/**
 * Keys no workspace file may set, at any depth. Connection and credentials
 * belong to ovcli.conf and the environment, full stop — this is the invariant
 * git spells `protected configuration`.
 */
export const FORBIDDEN_KEYS = [
  "url",
  "base_url",
  "mcp_url",
  "api_key",
  "bearer_token",
  "root_api_key",
  "gateway_token",
  "oidc_token",
  "ldap_username",
  "ldap_password",
  "account",
  "account_id",
  "user",
  "user_id",
  "auth_mode",
  "extra_headers",
  "credential_source",
  "cli_config_file",
  "config_file",
  // The camelCase spellings the harness loaders use. The projection into
  // harness knobs is an allowlist, so these could never take effect anyway —
  // but someone who writes `apiKey` here deserves to be told it was ignored,
  // not to have it vanish.
  "baseUrl",
  "mcpUrl",
  "apiKey",
  "bearerToken",
  "rootApiKey",
  "gatewayToken",
  "accountId",
  "userId",
  "authMode",
  "extraHeaders",
  "credentialSource",
  "credentialPath",
  "configPath",
];

/**
 * Sections whose keys are the user's own vocabulary, not ours. The ban below is
 * recursive so no section can smuggle a credential to a consumer that spreads
 * it into a request — but a free-form map's `user` key is a label, and
 * mangling it would be a bug, not a defence.
 */
export const FREE_FORM_SECTIONS = ["labels"];

const ENUMS = {
  "recall.peer_scope": ["all", "actor"],
  "peer.source": null, // free-form: preset name, template, or template array
};

const RANGES = {
  "recall.dedup_turns": { min: 0, max: 20, integer: true },
  "recall.max_items": { min: 1, max: 100, integer: true },
  "recall.score_threshold": { min: 0, max: 1, integer: false },
  "capture.commit_token_threshold": { min: 1000, max: 1_000_000, integer: true },
};

/**
 * The workspace schema in the vocabulary the harness loaders already speak.
 *
 * Only knobs a loader actually reads appear here — the same rule the ovcli
 * `plugin` section follows, so the schema never advertises a setting that
 * silently does nothing. `labels` is metadata for humans and is not projected.
 */
const KNOB_MAP = {
  "peer.id": "peerId",
  "peer.source": "peerSource",
  "recall.enabled": "autoRecall",
  "recall.peer_scope": "recallPeerScope",
  "recall.dedup_turns": "recallDedupTurns",
  "recall.max_items": "recallLimit",
  "recall.score_threshold": "scoreThreshold",
  "capture.enabled": "autoCapture",
  "capture.commit_token_threshold": "commitTokenThreshold",
  "bypass.session_patterns": "bypassSessionPatterns",
};

export const WORKSPACE_SCHEMA_KEYS = Object.keys(KNOB_MAP);

/** -1, 0 or 1 over dotted numeric versions; a non-numeric tail is ignored. */
function compareVersions(left, right) {
  const parse = (value) => String(value || "").split(".").map((part) => Number.parseInt(part, 10) || 0);
  const a = parse(left);
  const b = parse(right);
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const diff = (a[i] || 0) - (b[i] || 0);
    if (diff) return diff < 0 ? -1 : 1;
  }
  return 0;
}

/**
 * `min_client_version` warns and never blocks. A committed file that could stop
 * an older plugin from running would be a denial of service anyone with commit
 * access could mount, so it says "this was written for a newer client" and the
 * settings still apply.
 */
export function checkMinClientVersion(declared, clientVersion, warnings = []) {
  const required = String(declared || "").trim();
  const current = String(clientVersion || "").trim();
  if (!required || !current) return true;
  if (compareVersions(current, required) >= 0) return true;
  warnings.push(
    `this workspace asks for OpenViking plugin ${required} and this one is ${current}; `
    + "settings it introduced will be ignored rather than blocking the session",
  );
  return false;
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/**
 * Strip banned keys wherever they appear, collecting one warning per hit. They
 * are removed rather than rejected: a file is not made unusable by carrying a
 * key this layer refuses to honour.
 */
function stripForbidden(value, banned, warnings, path = "", depth = 0) {
  if (depth > MAX_DEPTH) throw new RangeError(`nested more than ${MAX_DEPTH} levels at '${path}'`);
  if (Array.isArray(value)) {
    return value.map((item, index) => stripForbidden(item, banned, warnings, `${path}[${index}]`, depth + 1));
  }
  if (!isPlainObject(value)) return value;

  const out = {};
  for (const [key, child] of Object.entries(value)) {
    const here = path ? `${path}.${key}` : key;
    if (UNSAFE_KEYS.includes(key)) {
      warnings.push(`ignored '${here}': a config file may not reach the object prototype`);
      continue;
    }
    if (banned.includes(key)) {
      warnings.push(`ignored '${here}': connection and credential settings belong in ovcli.conf or the environment`);
      continue;
    }
    // Matched on the key itself, not on depth: a registry entry keeps the same
    // section under `settings.`, and it must not end up stricter than the
    // committed file it outranks.
    out[key] = FREE_FORM_SECTIONS.includes(key)
      ? stripUnsafeOnly(child, warnings, here, depth + 1)
      : stripForbidden(child, banned, warnings, here, depth + 1);
  }
  return out;
}

/** A free-form section keeps its own vocabulary, minus the prototype keys. */
function stripUnsafeOnly(value, warnings, path, depth) {
  return stripForbidden(value, [], warnings, path, depth);
}

/**
 * Read one layer file. Every failure is a warning and an empty layer — a hook
 * must not die because a config file is odd.
 */
export function readWorkspaceFile(path, { root = "", layer = "" } = {}) {
  const warnings = [];
  const empty = { path, layer, exists: false, data: null, warnings };

  let stat;
  try {
    stat = statSync(path);
  } catch {
    return empty;
  }
  empty.exists = true;
  if (!stat.isFile()) {
    warnings.push(`${path} is not a regular file`);
    return empty;
  }
  if (stat.size > MAX_CONFIG_BYTES) {
    warnings.push(`${path} is larger than ${MAX_CONFIG_BYTES} bytes`);
    return empty;
  }
  // A symlink out of the workspace would let a repository read a file the user
  // never meant to expose to it.
  if (root) {
    try {
      const rel = relative(realpathSync(root), realpathSync(path));
      if (!rel || rel.startsWith("..") || isAbsolute(rel)) {
        warnings.push(`${path} resolves outside the workspace`);
        return empty;
      }
    } catch {
      warnings.push(`${path} could not be resolved`);
      return empty;
    }
  }

  let parsed;
  try {
    // Deliberately bare: no `${VAR}` expansion reaches a workspace file.
    parsed = JSON.parse(readFileSync(path, "utf-8"));
  } catch (err) {
    warnings.push(`${path} is not valid JSON (${err?.message || err})`);
    return empty;
  }
  if (!isPlainObject(parsed)) {
    warnings.push(`${path} must contain a JSON object`);
    return empty;
  }
  if (parsed.version !== CONFIG_VERSION) {
    warnings.push(`${path} declares version ${JSON.stringify(parsed.version)}; this client understands ${CONFIG_VERSION}`);
    return empty;
  }

  const { version, $schema, min_client_version: minClientVersion, ...rest } = parsed;
  let data;
  try {
    data = stripForbidden(rest, FORBIDDEN_KEYS, warnings);
  } catch (err) {
    warnings.push(`${path} is nested too deeply (${err?.message || err})`);
    return empty;
  }
  if (minClientVersion) data.min_client_version = String(minClientVersion);

  return { path, layer, exists: true, data, warnings };
}

function shadow(provenance, here, value, source) {
  const previous = provenance[here];
  const shadowed = previous ? [{ value: previous.value, source: previous.source }, ...previous.shadowed] : [];
  provenance[here] = { value, source, shadowed };
  return provenance[here];
}

function mergeInto(target, source, layer, provenance, path = "", depth = 0) {
  if (depth > MAX_DEPTH) throw new RangeError(`nested more than ${MAX_DEPTH} levels at '${path}'`);
  for (const [key, value] of Object.entries(source)) {
    if (UNSAFE_KEYS.includes(key)) continue;
    const here = path ? `${path}.${key}` : key;

    if (isPlainObject(value)) {
      // A section replacing a scalar is still a change of the effective value,
      // so the scalar has to be recorded as shadowed rather than left standing
      // in provenance as if it were still in force.
      if (!isPlainObject(target[key])) {
        if (target[key] !== undefined) shadow(provenance, here, "(section)", layer);
        target[key] = {};
      }
      mergeInto(target[key], value, layer, provenance, here, depth + 1);
      continue;
    }

    if (Array.isArray(value)) {
      // `"!reset"` drops everything the lower layers contributed, the way
      // EditorConfig's `unset` and git's empty `safe.directory` do.
      const reset = value[0] === "!reset";
      const incoming = reset ? value.slice(1) : value;
      const inheritable = !reset && Array.isArray(target[key]);
      const merged = inheritable ? [...target[key]] : [];
      for (const item of incoming) if (!merged.includes(item)) merged.push(item);

      // Only a genuine union credits both layers. A list landing on a scalar,
      // or on nothing, belongs to this layer alone.
      const previous = provenance[here];
      if (inheritable && previous) {
        provenance[here] = {
          value: merged,
          source: [previous.source, layer].filter(Boolean).join(" + "),
          shadowed: previous.shadowed,
        };
      } else {
        shadow(provenance, here, merged, reset ? `${layer} (reset)` : layer);
      }
      target[key] = merged;
      continue;
    }

    shadow(provenance, here, value, layer);
    target[key] = value;
  }
}

/**
 * Merge layers given lowest-precedence first, returning the effective value
 * plus, for every key, where it came from and what it covered up — the same
 * question `git config --show-origin --show-scope` answers.
 */
export function mergeConfigLayers(layers, warnings = []) {
  const value = {};
  const provenance = {};
  for (const { layer, data } of layers) {
    if (!isPlainObject(data)) continue;
    try {
      mergeInto(value, data, layer, provenance);
    } catch (err) {
      // One pathological layer must not take the others down with it.
      warnings.push(`skipped ${layer}: ${err?.message || err}`);
    }
  }
  return { value, provenance };
}

function get(object, path) {
  return path.split(".").reduce((node, key) => (isPlainObject(node) ? node[key] : undefined), object);
}

function set(object, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  let node = object;
  for (const key of keys) {
    if (!isPlainObject(node[key])) node[key] = {};
    node = node[key];
  }
  node[last] = value;
}

/**
 * Clamp numbers and reject unknown enum values, warning once per key.
 *
 * A repository can raise a cost knob, so the ceiling is enforced here rather
 * than trusted; an out-of-range value is clamped instead of rejected so a typo
 * degrades rather than disables.
 */
export function normalizeWorkspaceConfig(value, warnings = []) {
  for (const [path, { min, max, integer }] of Object.entries(RANGES)) {
    const raw = get(value, path);
    if (raw === undefined) continue;
    // `Number()` turns null, true and [] into finite numbers, which would
    // silently pin a knob to a bound instead of reporting a bad value.
    const number = typeof raw === "number" || typeof raw === "string" ? Number(raw) : NaN;
    if (!Number.isFinite(number)) {
      warnings.push(`ignored '${path}': ${JSON.stringify(raw)} is not a number`);
      set(value, path, undefined);
      continue;
    }
    const clamped = Math.min(max, Math.max(min, integer ? Math.floor(number) : number));
    if (clamped !== number) warnings.push(`clamped '${path}' from ${number} to ${clamped} (allowed ${min}..${max})`);
    set(value, path, clamped);
  }

  for (const [path, allowed] of Object.entries(ENUMS)) {
    if (!allowed) continue;
    const raw = get(value, path);
    if (raw === undefined) continue;
    if (!allowed.includes(raw)) {
      warnings.push(`ignored '${path}': ${JSON.stringify(raw)} is not one of ${allowed.join(", ")}`);
      set(value, path, undefined);
    }
  }
  return value;
}

/**
 * Flatten the merged workspace config into the flat knobs a harness loader
 * reads, so the new layers slot in exactly where the ovcli `plugin` section
 * already does and everything downstream — including the env vars that still
 * win — keeps working unchanged.
 */
export function projectWorkspaceSettings(value) {
  const settings = {};
  for (const [path, knob] of Object.entries(KNOB_MAP)) {
    const raw = get(value, path);
    if (raw !== undefined) settings[knob] = raw;
  }
  return settings;
}

export function workspaceConfigPaths(root) {
  if (!root) return [];
  const dir = join(root, CONFIG_DIR_NAME);
  return [
    { layer: `${TEAM_FILE} (workspace)`, path: join(dir, TEAM_FILE) },
    { layer: `${LOCAL_FILE} (workspace)`, path: join(dir, LOCAL_FILE) },
  ];
}

/**
 * The two workspace-file layers, lowest precedence first. Callers stack the
 * registry above these and the ovcli.conf plugin section below them.
 */
export function loadWorkspaceLayers(root, { clientVersion = "" } = {}) {
  const warnings = [];
  const layers = [];
  for (const { layer, path } of workspaceConfigPaths(root)) {
    const file = readWorkspaceFile(path, { root, layer });
    warnings.push(...file.warnings);
    if (!file.data) continue;
    const { min_client_version: declared, ...data } = file.data;
    checkMinClientVersion(declared, clientVersion, warnings);
    layers.push({ layer, data, path });
  }
  return { layers, warnings };
}

/**
 * Settings a workspace file turned off. Announced at session start and in
 * doctor, because trusting the file is a decision the user should be able to
 * see rather than one made silently on their behalf.
 */
export function announcedOverrides(provenance) {
  const announced = [];
  for (const [key, entry] of Object.entries(provenance)) {
    const fromWorkspace = String(entry.source || "").includes("(workspace)");
    if (!fromWorkspace) continue;
    if (entry.value === false || key === "peer.id" || key === "peer.source" || key.startsWith("bypass.")) {
      announced.push({ key, value: entry.value, source: entry.source });
    }
  }
  return announced;
}
