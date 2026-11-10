/**
 * Twenty-odd files tell a user or an agent how to give a directory its own
 * peer. This keeps them from drifting apart: the snippet has to be the same
 * snippet everywhere, the retired fallback must be gone everywhere, and the
 * variables the docs list have to be the ones the code actually substitutes.
 *
 * It asserts none of the prose. Rewording a page must not turn this red.
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

import { WORKSPACE_PEER_HINT } from "./lib/doctor-core.mjs";
import { resolveWorkspaceIdentity } from "./lib/workspace-identity.mjs";
import { PEER_SOURCE_PRESETS } from "./lib/workspace-peer.mjs";
import { ROOT } from "./sync.mjs";

const SKIP_DIRS = new Set(["node_modules", ".git", ".vitepress", "design"]);

function walk(dir, match, out = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) walk(path, match, out);
    } else if (match(path)) {
      out.push(path);
    }
  }
  return out;
}

const REFERENCES = [
  "examples/claude-code-memory-plugin/skills/ov-memory-doctor/reference.md",
  "examples/codex-memory-plugin/skills/ov-memory-doctor/reference.md",
];

test("every surface that teaches the peer file quotes the same snippet", () => {
  const carriers = [
    "examples/skills/openviking-memory/SKILL.md",
    ...REFERENCES,
    "docs/en/configuration/02-client.md",
    "docs/zh/configuration/02-client.md",
    "docs/en/agent-integrations/02-claude-code.md",
    "docs/zh/agent-integrations/02-claude-code.md",
  ];
  for (const rel of carriers) {
    const text = readFileSync(join(ROOT, rel), "utf-8");
    assert.ok(text.includes(WORKSPACE_PEER_HINT), `${rel} must carry ${WORKSPACE_PEER_HINT} byte for byte`);
  }
});

test("no document still promises the working directory, or a command that does not exist", () => {
  const files = [
    ...walk(join(ROOT, "docs"), (p) => p.endsWith(".md")),
    ...walk(join(ROOT, "examples"), (p) => /README(_CN)?\.md$/.test(p)),
    ...walk(join(ROOT, "examples", "skills"), (p) => p.endsWith(".md")),
    ...REFERENCES.map((rel) => join(ROOT, rel)),
    join(ROOT, "examples/schemas/workspace-config-v1.json"),
    join(ROOT, "examples/workspace-config.example.json"),
    // The shared library ships to eight plugins; a comment naming a command
    // that does not exist travels just as far as a document does.
    ...walk(join(ROOT, "examples", "memory-plugin-shared", "lib"), (p) => p.endsWith(".mjs")),
  ];
  assert.ok(files.length > 20, "the sweep found suspiciously few files");

  const retiredChain = /\{git_remote\}"?,\s*"?\{git_root\}"?,\s*"?\{cwd\}/;
  const withdrawnCommand = /\bov (workspace|peer)\b/;
  // The changelogs are generated from GitHub Releases, so a release note that
  // happens to name one of these commands is not something a human can edit here.
  const generated = /docs\/(en|zh)\/about\/02-changelog\.md$/;
  for (const path of new Set(files)) {
    const rel = relative(ROOT, path);
    const text = readFileSync(path, "utf-8");
    assert.ok(!retiredChain.test(text), `${rel} still spells the default chain with {cwd} in it`);
    if (generated.test(rel)) continue;
    assert.ok(!withdrawnCommand.test(text), `${rel} names an ov workspace / ov peer command, which does not exist`);
  }
});

test("the canonical page agrees with the code it documents", () => {
  const page = readFileSync(join(ROOT, "docs/en/configuration/02-client.md"), "utf-8");
  const rendered = `[${PEER_SOURCE_PRESETS.git.map((t) => `"${t}"`).join(", ")}]`;
  assert.ok(page.includes(rendered), `the page must spell the default chain as ${rendered}`);

  // `harness` is the one variable the caller supplies rather than the identity,
  // which is cached under a cwd-only key and so cannot hold it.
  const known = new Set([...Object.keys(resolveWorkspaceIdentity({ cwd: ROOT, cache: false }).vars), "harness"]);
  const start = page.indexOf("### Workspace Peer");
  const end = page.indexOf("### What a Workspace File May Not Set");
  assert.ok(start >= 0, "the page no longer has a '### Workspace Peer' heading");
  assert.ok(end >= 0, "the page no longer has a '### What a Workspace File May Not Set' heading");
  const section = page.slice(start, end);
  const documented = new Set([...section.matchAll(/\{([a-z_]+)\}/g)].map((m) => m[1]));
  assert.ok(documented.size >= 4, "the variable table went missing");
  for (const name of documented) {
    assert.ok(known.has(name), `the page documents {${name}}, which no identity variable provides`);
  }
});
