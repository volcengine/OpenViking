import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

// The shared request builder only sends `session_id` — and with it `dedup_turns`
// and server-side query expansion — when the caller supplies one. Every harness
// that already owns an OV session id has to forward it at its recall call site,
// which no harness unit test covers end to end: they each stub the transport
// below the point where the id would have been added. These assertions pin the
// wiring so a harness cannot silently fall back to stateless recall again.
const CALL_SITES = [
  {
    name: "ZCode hook forwards its derived session id",
    file: join(ROOT, "examples", "zcode-memory-plugin", "scripts", "zcode-hook.mjs"),
    pattern: /recallForPrompt\([^)]*\{[^}]*\bsessionId\b/s,
  },
  {
    name: "OpenCode recall forwards the mapped OV session id",
    file: join(ROOT, "examples", "opencode-plugin", "lib", "memory-recall.mjs"),
    pattern: /sessionId:\s*sessionID\s*\?\s*sessionManager\.getMappedSessionId\(sessionID\)/,
  },
  {
    name: "pi recall forwards the sync manager's session id",
    file: join(ROOT, "examples", "pi-coding-agent-extension", "recall.ts"),
    pattern: /sessionId:\s*this\.sessionId\(\)/,
  },
];

for (const { name, file, pattern } of CALL_SITES) {
  test(name, async () => {
    const source = await readFile(file, "utf-8");
    assert.match(source, pattern);
  });
}

test("pi wires the sync manager into the recall manager", async () => {
  const source = await readFile(join(ROOT, "examples", "pi-coding-agent-extension", "index.ts"), "utf-8");
  // Only the session-id getter is asserted; later arguments (the recall
  // ledger) come and go without changing what this test is about.
  assert.match(source, /new RecallManager\(\s*client,\s*config,\s*\(\)\s*=>\s*sync\.sessionId,/);
  // The getter exists because SyncManager has to be constructed first.
  assert.ok(
    source.indexOf("new SyncManager(") < source.indexOf("new RecallManager("),
    "SyncManager must be constructed before RecallManager reads its session id",
  );
});

test("OpenCode passes the session manager into the recall component", async () => {
  const source = await readFile(join(ROOT, "examples", "opencode-plugin", "index.mjs"), "utf-8");
  assert.match(source, /createMemoryRecall\(\{\s*config,\s*sessionManager\s*\}\)/);
});

// Forwarding a session id makes the server spend the query-expansion fuse, so
// an adapter that pins its own HTTP timeout aborts a request the server is
// still inside and drops back to the path without dedup or expansion. Each
// adapter's own budget has to stay a default, not a ceiling.
const TIMEOUT_PASSTHROUGH = [
  {
    name: "OpenCode keeps the per-request deadline the helper hands down",
    file: join(ROOT, "examples", "opencode-plugin", "lib", "memory-recall.mjs"),
    pattern: /timeoutMs:\s*options\.timeoutMs\s*\?\?\s*\d+/,
  },
  {
    name: "pi keeps the per-request deadline the helper hands down",
    file: join(ROOT, "examples", "pi-coding-agent-extension", "recall.ts"),
    pattern: /fetchJSON\(path,\s*init,\s*options\?\.timeoutMs\s*\?\?\s*\d+\)/,
  },
];

for (const { name, file, pattern } of TIMEOUT_PASSTHROUGH) {
  test(name, async () => {
    const source = await readFile(file, "utf-8");
    assert.match(source, pattern);
  });
}

const QUERY_EXPANSION_OPT_OUT = [
  {
    name: "OpenCode exposes the query-expansion opt-out",
    file: join(ROOT, "examples", "opencode-plugin", "lib", "config.mjs"),
  },
  {
    name: "pi exposes the query-expansion opt-out",
    file: join(ROOT, "examples", "pi-coding-agent-extension", "config.ts"),
  },
];

for (const { name, file } of QUERY_EXPANSION_OPT_OUT) {
  test(name, async () => {
    const source = await readFile(file, "utf-8");
    // The env var alone is not enough: buildContextSearchBody only emits
    // `query_expansion` when the harness also marks it as configured.
    assert.match(source, /OPENVIKING_RECALL_QUERY_EXPANSION/);
    assert.match(source, /recallQueryExpansionConfigured/);
  });
}
