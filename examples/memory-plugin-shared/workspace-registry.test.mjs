import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  entryPath,
  identityKey,
  readEntry,
  registryDir,
  slotName,
} from "./lib/workspace-registry.mjs";

async function home() {
  return { OPENVIKING_HOME: await mkdtemp(join(tmpdir(), "ov-registry-")) };
}

const repo = { isGit: true, remote: "github.com/volcengine/openviking", gitCommonDir: "/x/.git" };
const otherRepo = { isGit: true, remote: "github.com/someone/else", gitCommonDir: "/x/.git" };

/** Nothing writes the registry; a user creates an entry by hand. */
async function putEntry(root, env, identity, entry) {
  await mkdir(registryDir(env), { recursive: true });
  await writeFile(entryPath(root, env, identity), JSON.stringify({ version: 1, ...entry }, null, 2));
}

test("a slot is readable and still unique per absolute path", () => {
  const a = slotName("/Users/x/src/api");
  const b = slotName("/Users/x/work/api");
  assert.match(a, /^api-[0-9a-f]{12}\.json$/);
  assert.notEqual(a, b, "same basename, different path, different slot");
  assert.equal(a, slotName("/Users/x/src/api"), "slots are stable");
  assert.match(slotName("/"), /^[0-9a-f]{12}\.json$/);
});

test("two worktrees of one repository share a slot; two clones do not", () => {
  // A linked worktree is a second checkout of the same repository — the same
  // peer, so the same settings.
  const main = slotName("/Users/x/src/api", repo);
  const worktree = slotName("/Users/x/wt/api-feature", repo);
  assert.equal(worktree, main, "the checkout path must not split one workspace in two");
  assert.match(main, /^openviking-[0-9a-f]{12}\.json$/);

  assert.notEqual(slotName("/Users/x/src/api", otherRepo), main, "a different repository is a different slot");

  // Without a repository there is no identity but the path.
  const plainA = slotName("/Users/x/src/notes", { isGit: false });
  const plainB = slotName("/Users/x/work/notes", { isGit: false });
  assert.notEqual(plainA, plainB);
});

test("a hand-written entry reads back whole", async () => {
  const env = await home();
  const root = "/Users/x/src/api";
  await putEntry(root, env, repo, {
    root,
    identity: identityKey(repo),
    settings: { recall: { max_items: 7 } },
    peer: { id: "pinned" },
  });

  const { entry, warnings } = readEntry(root, { identity: repo, env });
  assert.deepEqual(entry.settings, { recall: { max_items: 7 } });
  assert.deepEqual(entry.peer, { id: "pinned" });
  assert.deepEqual(warnings, []);
});

test("a missing entry is a miss, not a failure", async () => {
  const env = await home();
  const { entry, warnings, conflict } = readEntry("/Users/x/src/api", { identity: repo, env });
  assert.equal(entry, null);
  assert.equal(conflict, false);
  assert.deepEqual(warnings, []);
});

test("a path reused by a different repository does not inherit the old peer", async () => {
  const env = await home();
  const root = "/Users/x/src/api";
  await putEntry(root, env, repo, { identity: identityKey(repo), peer: { id: "from-the-old-repo" } });

  // Keying the slot on identity makes the crossing physically impossible: the
  // new repository looks in a different file and finds nothing.
  const miss = readEntry(root, { identity: otherRepo, env });
  assert.equal(miss.entry, null);
  assert.notEqual(entryPath(root, env, otherRepo), entryPath(root, env, repo));

  const hit = readEntry(root, { identity: repo, env });
  assert.equal(hit.entry.peer.id, "from-the-old-repo", "the matching identity still reads it");
});

test("an entry whose recorded identity contradicts the caller is still refused", async () => {
  // Slot isolation is the first defence; this is the second, for a file that
  // was hand-edited or moved.
  const env = await home();
  const root = "/Users/x/src/api";
  await putEntry(root, env, repo, { identity: identityKey(otherRepo), peer: { id: "someone-elses" } });

  const miss = readEntry(root, { identity: repo, env });
  assert.equal(miss.entry, null);
  assert.equal(miss.conflict, true);
  assert.ok(miss.warnings.some((w) => w.includes("different repository")));
});

test("identityKey prefers the remote and degrades honestly", () => {
  assert.equal(identityKey(repo), `remote:${repo.remote}`);
  assert.equal(identityKey({ isGit: true, gitCommonDir: "/x/.git" }), "git:/x/.git");
  assert.equal(identityKey({ isGit: false }), "path");
  assert.equal(identityKey(null), "path");
});

// --- regressions found by review ------------------------------------------

test("re-spelling origin is not a different repository", () => {
  const ssh = { isGit: true, remote: "github.com/o/r", gitCommonDir: "/x/.git" };
  const https = { isGit: true, remote: "github.com/o/r", gitCommonDir: "/x/.git" };
  assert.equal(identityKey(ssh), identityKey(https));
});

test("an entry from a newer client is skipped rather than half-read", async () => {
  const env = await home();
  const root = "/Users/x/src/api";
  await mkdir(registryDir(env), { recursive: true });
  await writeFile(
    entryPath(root, env, repo),
    JSON.stringify({ version: 2, peer: { id: "pinned" }, important: "future" }),
  );

  const { entry, warnings } = readEntry(root, { identity: repo, env });
  assert.equal(entry, null);
  assert.ok(warnings.some((w) => w.includes("understands 1")));
});

test("a free-form section in the registry keeps its own vocabulary", async () => {
  const env = await home();
  const root = "/Users/x/src/api";
  await putEntry(root, env, repo, {
    identity: identityKey(repo),
    settings: { labels: { user: "alice", team: "core" } },
  });

  const { entry, warnings } = readEntry(root, { identity: repo, env });
  assert.deepEqual(entry.settings.labels, { user: "alice", team: "core" });
  assert.deepEqual(warnings, []);
});
