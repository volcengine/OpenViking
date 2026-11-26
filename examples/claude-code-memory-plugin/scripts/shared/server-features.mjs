// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Which OpenViking server capabilities a server has, by its version.
 *
 * `server-features.json` lists each capability a client depends on with the
 * first server version that has it; the gateway ships the same file
 * (`openviking_gateway/server-features.json`), and the sync keeps both copies
 * byte-identical. Callers ask `supports` before using a capability; they do
 * not infer support from a 404.
 *
 * Exports:
 *
 *   SERVER_FEATURES                       the table, frozen
 *   requiredVersion(feature) -> string    the first version with `feature`
 *   supports(feature, version) -> boolean
 *     Compares the release part of `version` (what `/health` reports, so
 *     `0.5.7.dev44` counts as 0.5.7) with the table. A version that cannot be
 *     read does not support anything; "readable" is PEP 440, the grammar the
 *     gateway's reader (Python `packaging`) accepts, so both answer the same
 *     for the same string. An unknown feature name throws: it is a programming
 *     error, not a property of the server.
 *   featureStatus(feature, version) -> { feature, supported, required, actual }
 *     The same answer with what a message to the user needs; `actual` is the
 *     version as given, or null.
 *   healthVersion(fetchJSON) -> Promise<string | null>
 *     The version from `GET /health` through a plugin's `fetchJSON`
 *     (`ov-http.mjs`: resolves `{ ok, result }`, never throws); null when the
 *     health check fails or reports no version.
 */
import { readFileSync } from "node:fs";

const TABLE = JSON.parse(readFileSync(new URL("./server-features.json", import.meta.url), "utf-8"));

export const SERVER_FEATURES = Object.freeze({ ...TABLE });

// packaging.version.VERSION_PATTERN, anchored and case-insensitive as
// `Version` uses it. Only the epoch and the release are read; the rest is
// matched so that a string Python rejects is rejected here too.
const PEP440 = new RegExp(
  "^v?(?:(\\d+)!)?(\\d+(?:\\.\\d+)*)"
    + "(?:[-_.]?(?:alpha|a|beta|b|preview|pre|c|rc)[-_.]?\\d*)?"
    + "(?:-\\d+|[-_.]?(?:post|rev|r)[-_.]?\\d*)?"
    + "(?:[-_.]?dev[-_.]?\\d*)?"
    + "(?:\\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?$",
  "i",
);

/** `[epoch, ...release]` of a version string, or null when it is not one. */
function release(version) {
  if (typeof version !== "string") return null;
  const match = PEP440.exec(version.trim());
  if (!match) return null;
  return [Number(match[1] ?? 0), ...match[2].split(".").map(Number)];
}

export function requiredVersion(feature) {
  if (!Object.prototype.hasOwnProperty.call(SERVER_FEATURES, feature)) {
    throw new Error(`unknown server feature: ${String(feature)}`);
  }
  return SERVER_FEATURES[feature];
}

export function supports(feature, version) {
  const need = release(requiredVersion(feature));
  const have = release(version);
  if (!have) return false;
  for (let i = 0; i < Math.max(have.length, need.length); i += 1) {
    const a = have[i] ?? 0;
    const b = need[i] ?? 0;
    if (a !== b) return a > b;
  }
  return true;
}

export function featureStatus(feature, version) {
  return {
    feature,
    supported: supports(feature, version),
    required: requiredVersion(feature),
    actual: typeof version === "string" && version ? version : null,
  };
}

export async function healthVersion(fetchJSON, { timeoutMs = 5000 } = {}) {
  try {
    const res = await fetchJSON("/health", undefined, { timeoutMs });
    const version = res?.ok ? res.result?.version : undefined;
    return typeof version === "string" && version ? version : null;
  } catch {
    return null;
  }
}
