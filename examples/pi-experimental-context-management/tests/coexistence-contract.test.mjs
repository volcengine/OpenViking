/**
 * The coexistence contract between this fork and the non-experimental
 * extension, pinned at the text level.
 *
 * The two pi extensions must never drive the same OpenViking session: both
 * would capture every message and both would race on the same commit. This
 * fork therefore stands down as soon as it detects the peer, on two signals —
 * a search tool registered from another extension's directory, and the
 * `globalThis.__OPENVIKING_PI_EXTENSION__` marker the peer sets once its
 * health check passes. The marker is the signal that matters: on a `/mcp`
 * 401/403 the peer registers zero tools and still writes the session, so no
 * tool name is there to see.
 *
 * The behaviour of both signals is covered by the runtime tests in
 * `index-load.test.mjs`, but those load `index.ts` through the jiti pi bundles
 * and skip themselves when they cannot find it — which is exactly what happens
 * on CI, where pi is not installed (that file then reports every coexistence
 * case as SKIPPED and exits green). So on CI nothing today notices if one side
 * renames the marker or the probe set loses a tool name.
 *
 * This file closes that hole by reading the two `index.ts` files and the peer's
 * `tools.ts` as TEXT and asserting the literals they must agree on. Reading
 * source rather than running it is deliberate: the contract is a constant
 * shared across two extensions that are never loaded in the same process here,
 * and a text assertion is the only form of it that runs without jiti. It is a
 * stand-in for the runtime tests, not a replacement — keep both.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const EXTENSION_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const FORK_INDEX = join(EXTENSION_DIR, "index.ts");

/**
 * The non-experimental extension, as a sibling of this one in the repository.
 *
 * An installed copy of this fork ships alone, so the peer's sources are simply
 * absent there; the assertions that need them skip instead of failing. In the
 * repository — and therefore on CI, which is where this file has to bite —
 * both directories are always present.
 */
const PEER_DIR = join(dirname(EXTENSION_DIR), "pi-coding-agent-extension");
const PEER_INDEX = join(PEER_DIR, "index.ts");
const PEER_TOOLS = join(PEER_DIR, "tools.ts");
const NO_PEER_SOURCES =
  existsSync(PEER_INDEX) && existsSync(PEER_TOOLS)
    ? false
    : `the peer extension is not beside this one (${PEER_DIR}); an installed copy ships alone`;

function source(file) {
  return readFileSync(file, "utf-8");
}

/** The names in `COEXISTENCE_PROBE_TOOLS`, in source order. */
function probeToolNames(text) {
  const match = /COEXISTENCE_PROBE_TOOLS\s*=\s*new Set\(\s*\[([\s\S]*?)\]\s*\)/.exec(text);
  assert.ok(
    match,
    "index.ts no longer declares COEXISTENCE_PROBE_TOOLS as a set literal. It is the list of " +
      "tool names that mean the non-experimental extension is loaded; if it moved, move this " +
      "assertion with it rather than deleting it.",
  );
  return [...match[1].matchAll(/["'`]([^"'`]+)["'`]/g)].map((entry) => entry[1]);
}

/** The string `PEER_GLOBAL_MARKER` is bound to. */
function peerMarkerName(text) {
  const match = /PEER_GLOBAL_MARKER\s*=\s*["'`]([^"'`]+)["'`]/.exec(text);
  assert.ok(
    match,
    "index.ts no longer declares PEER_GLOBAL_MARKER as a string literal. That constant is the " +
      "globalThis key the peer sets; it has to stay readable from here so this test can check " +
      "that the peer still writes the same key.",
  );
  return match[1];
}

/**
 * The body of `peerOwnsToolSurface()`.
 *
 * Bounded on purpose: an assertion about what the probe reads must not be
 * satisfied by an unrelated mention elsewhere in a 650-line file.
 */
function peerProbeBody(text) {
  const start = text.indexOf("const peerOwnsToolSurface");
  assert.notEqual(
    start,
    -1,
    "index.ts no longer defines peerOwnsToolSurface(). That function is how this fork notices " +
      "the other OpenViking extension and stands down; without it both extensions sync the same " +
      "OpenViking session.",
  );
  const end = text.indexOf("\n  };", start);
  return end === -1 ? text.slice(start, start + 4000) : text.slice(start, end);
}

// ---------------------------------------------------------------------------
// Signal 1: the peer's search tool, under both the old and the new name
// ---------------------------------------------------------------------------

test("the probe set names the peer's search tool under both its old and its current name", () => {
  const fork = source(FORK_INDEX);
  const names = probeToolNames(fork);

  assert.ok(
    names.includes("viking_search"),
    "COEXISTENCE_PROBE_TOOLS dropped `viking_search`. The peer registered that name before 0.4 " +
      "and such a build may still be installed; without it in the probe set this fork does not " +
      "see an old peer and both extensions capture every message into the same OpenViking " +
      "session.",
  );
  assert.ok(
    names.includes("openviking_search"),
    "COEXISTENCE_PROBE_TOOLS dropped `openviking_search`. That is what the peer registers from " +
      "0.4 on (tools.ts prefixes the server's MCP tool names with `openviking_`); without it in " +
      "the probe set the current peer is invisible to the tool-name signal and two writers race " +
      "on one session commit.",
  );

  assert.match(
    peerProbeBody(fork),
    /COEXISTENCE_PROBE_TOOLS/,
    "peerOwnsToolSurface() no longer consults COEXISTENCE_PROBE_TOOLS, so the probe set has no " +
      "effect: a peer that only announces itself through its tools would go unnoticed.",
  );
});

// ---------------------------------------------------------------------------
// Signal 2: the globalThis marker — the one that survives a 403
// ---------------------------------------------------------------------------

test("the fork reads the peer's globalThis marker, not only its tool names", () => {
  const fork = source(FORK_INDEX);

  assert.equal(
    peerMarkerName(fork),
    "__OPENVIKING_PI_EXTENSION__",
    "PEER_GLOBAL_MARKER no longer matches the key the peer writes " +
      "(`globalThis.__OPENVIKING_PI_EXTENSION__` in pi-coding-agent-extension/index.ts). Both " +
      "sides have to spell it identically; a rename on either side silently disables the only " +
      "coexistence signal that works when the peer has no tools.",
  );

  const body = peerProbeBody(fork);
  assert.match(
    body,
    /PEER_GLOBAL_MARKER|__OPENVIKING_PI_EXTENSION__/,
    "peerOwnsToolSurface() no longer looks at the peer's globalThis marker. Tool names alone are " +
      "not enough: when `/mcp` answers 401/403 the peer registers ZERO tools and still writes the " +
      "OpenViking session, and the marker is the only thing left to see it by.",
  );
});

test("the peer still writes the globalThis marker this fork probes for", {
  skip: NO_PEER_SOURCES,
}, () => {
  const marker = peerMarkerName(source(FORK_INDEX));
  const peer = source(PEER_INDEX);

  assert.ok(
    peer.includes(marker),
    `pi-coding-agent-extension/index.ts no longer mentions \`${marker}\`, the marker this fork ` +
      "reads to decide whether the peer is active. If the peer stops announcing itself, this " +
      "fork keeps running beside it and both sync the same OpenViking session.",
  );

  const assignment = new RegExp(`${marker}(?:["'\`]?\\s*\\])?\\s*=\\s*[^=]`);
  assert.match(
    peer,
    assignment,
    `pi-coding-agent-extension/index.ts mentions \`${marker}\` but never assigns it. The marker ` +
      "is only useful as a write: the peer has to set it once its health check passes, before " +
      "any tool is registered, because a failed MCP handshake leaves it with no tools at all.",
  );

  const markerLine = peer.split("\n").find((line) => line.includes(marker) && line.includes("="));
  assert.match(
    markerLine ?? "",
    /globalThis/,
    `pi-coding-agent-extension/index.ts sets \`${marker}\` somewhere other than globalThis. This ` +
      "fork reads it off globalThis — the two extensions share a process but not a module graph, " +
      "so a module-scoped marker never reaches the reader.",
  );
});

// ---------------------------------------------------------------------------
// The peer's tool surface is really `openviking_`-prefixed
// ---------------------------------------------------------------------------

test("the peer's registered tool names carry the prefix the probe set expects", {
  skip: NO_PEER_SOURCES,
}, () => {
  const tools = source(PEER_TOOLS);
  const match = /TOOL_NAME_PREFIX\s*=\s*["'`]([^"'`]*)["'`]/.exec(tools);
  assert.ok(
    match,
    "pi-coding-agent-extension/tools.ts no longer declares TOOL_NAME_PREFIX as a string literal. " +
      "That prefix is what turns the server's MCP tool names into pi tool names, and this fork's " +
      "probe set is written against it.",
  );
  const prefix = match[1];

  assert.equal(
    prefix,
    "openviking_",
    "The peer changed its tool-name prefix. Every name in this fork's COEXISTENCE_PROBE_TOOLS is " +
      "built from it, so a prefix change makes the tool-name signal miss the peer entirely.",
  );

  assert.match(
    tools,
    /TOOL_NAME_PREFIX\s*\+/,
    "pi-coding-agent-extension/tools.ts declares TOOL_NAME_PREFIX but no longer builds the " +
      "registered names from it, so the names pi actually sees are no longer the ones this " +
      "fork's probe set was written against.",
  );

  const names = probeToolNames(source(FORK_INDEX));
  assert.ok(
    names.includes(`${prefix}search`),
    `COEXISTENCE_PROBE_TOOLS does not contain \`${prefix}search\`, the name the peer registers ` +
      "for the server's `search` tool. `search` is the one tool the peer has always exposed, so " +
      "it is the probe this fork relies on; without it a connected peer is only detectable " +
      "through the globalThis marker.",
  );
});
