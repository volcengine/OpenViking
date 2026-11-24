import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, symlink, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  entryPath,
  identityKey,
  openvikingHome,
  readEntry,
  registryDir,
  resolveConnectionPointer,
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

// --- the registry's connection pointer --------------------------------------

test("a registry entry may carry a connection pointer; a workspace file may not", async () => {
  const env = await home();
  const root = "/Users/x/src/api";
  await putEntry(root, env, repo, {
    identity: identityKey(repo),
    connection: { name: "work-agent" },
  });
  const { entry, warnings } = readEntry(root, { identity: repo, env });
  assert.deepEqual(entry.connection, { name: "work-agent" });
  assert.deepEqual(warnings, []);
});

test("resolveConnectionPointer expands a named connection into the library", async () => {
  const env = await home();
  const resolved = resolveConnectionPointer({ name: "work-agent" }, { env });
  assert.equal(resolved.path, join(openvikingHome(env), "connections", "work-agent.conf"));
  assert.deepEqual(resolved.warnings, []);
  // A name is a file name, not a path: no traversal out of the library.
  const escape = resolveConnectionPointer({ name: "../ovcli.conf" }, { env });
  assert.equal(escape.path, "");
  assert.ok(escape.warnings.some((w) => w.includes("connection name")));
});

test("a file pointer must stay inside the user's OpenViking home", async () => {
  const env = await home();
  const inside = resolveConnectionPointer({ file: "connections/dsh.conf" }, { env });
  assert.equal(inside.path, join(openvikingHome(env), "connections", "dsh.conf"));
  assert.deepEqual(inside.warnings, [],
    "a relative pointer resolves against the OpenViking home, not the launch directory");

  // `~` expands against the OS home, which is not the OpenViking home the
  // pointer is confined to — a home-scoped pointer spells `connections/…`.
  const outsideHome = resolveConnectionPointer({ file: "~/connections/dsh.conf" }, { env });
  assert.equal(outsideHome.path, "");
  assert.ok(outsideHome.warnings.some((w) => w.includes("must stay inside")));

  const outside = resolveConnectionPointer({ file: "/etc/ovcli.conf" }, { env });
  assert.equal(outside.path, "");
  assert.ok(outside.warnings.some((w) => w.includes("must stay inside")));

  const leaves = resolveConnectionPointer({ file: "connections/../../ovcli.conf" }, { env });
  assert.equal(leaves.path, "", "a traversal that leaves the home is refused, resolved or not");
  assert.ok(leaves.warnings.some((w) => w.includes("must stay inside")));

  const escape = resolveConnectionPointer({ file: "connections/../ovcli.conf" }, { env });
  assert.equal(escape.path, join(openvikingHome(env), "ovcli.conf"), "a relative pointer resolves, then is confined");
  assert.deepEqual(escape.warnings, []);

  const tilde = resolveConnectionPointer({ file: "~" }, { env });
  assert.equal(tilde.path, "", "the home directory itself is not a connection file");
});

test("a pointer's target escaping through a symlink is refused", async (t) => {
  if (process.platform === "win32") {
    t.skip("creating symlinks on Windows needs an elevated shell");
    return;
  }
  const env = await home();
  const home0 = openvikingHome(env);
  const outside = await mkdtemp(join(tmpdir(), "ov-escape-"));
  await symlink(outside, join(home0, "link"));
  const pointed = resolveConnectionPointer({ file: join(home0, "link", "ovcli.conf") }, { env });
  assert.equal(pointed.path, "", "the realpath escape must be caught even though the target does not exist yet");
  assert.ok(pointed.warnings.some((w) => w.includes("must stay inside")));
});

test("an unusable pointer warns instead of changing the chain", async () => {
  const env = await home();
  assert.equal(resolveConnectionPointer(null, { env }).path, "");
  assert.equal(resolveConnectionPointer(undefined, { env }).path, "");
  for (const connection of [{}, { other: 1 }, { name: "" }, { file: 5 }, { user: "x", api_key: "y" }]) {
    const resolved = resolveConnectionPointer(connection, { env });
    assert.equal(resolved.path, "");
    assert.ok(resolved.warnings.length > 0, `expected a warning for ${JSON.stringify(connection)}`);
  }
});
