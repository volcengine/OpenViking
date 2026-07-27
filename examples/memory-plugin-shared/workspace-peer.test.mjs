import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  deriveWorkspacePeerId,
  normalizeGitRemoteUrl,
  peerIdFromCanonical,
  resolveEffectivePeerId,
} from "./lib/workspace-peer.mjs";

const HAS_GIT = (() => {
  try {
    execFileSync("git", ["--version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
})();

// ----- normalizeGitRemoteUrl (pure) --------------------------------------

test("normalizeGitRemoteUrl: SSH scp-like and HTTPS collapse to the same canonical", () => {
  // Host is lowercased; path case is preserved by the normalizer.
  // peerIdFromCanonical lowercases the whole canonical before hashing.
  assert.equal(
    normalizeGitRemoteUrl("git@github.com:volcengine/OpenViking.git"),
    "github.com/volcengine/OpenViking",
  );
  assert.equal(
    normalizeGitRemoteUrl("https://github.com/volcengine/OpenViking.git"),
    "github.com/volcengine/OpenViking",
  );
  assert.equal(
    normalizeGitRemoteUrl("https://github.com/volcengine/OpenViking"),
    "github.com/volcengine/OpenViking",
  );
});

test("normalizeGitRemoteUrl: explicit ssh:// with port collapses with https", () => {
  assert.equal(
    normalizeGitRemoteUrl("ssh://git@github.com:22/volcengine/OpenViking.git"),
    "github.com/volcengine/OpenViking",
  );
  assert.equal(
    normalizeGitRemoteUrl("ssh://git@github.com/volcengine/OpenViking.git"),
    "github.com/volcengine/OpenViking",
  );
});

test("normalizeGitRemoteUrl: strips credentials, query, fragment, trailing slash, .git", () => {
  assert.equal(
    normalizeGitRemoteUrl("https://user:token@github.com/volcengine/OpenViking.git?x=1#y"),
    "github.com/volcengine/OpenViking",
  );
  assert.equal(
    normalizeGitRemoteUrl("https://github.com/volcengine/OpenViking/"),
    "github.com/volcengine/OpenViking",
  );
  assert.equal(
    normalizeGitRemoteUrl("git://git@github.com/volcengine/OpenViking.git"),
    "github.com/volcengine/OpenViking",
  );
});

test("normalizeGitRemoteUrl: case-insensitive host; path preserved through canonicalization", () => {
  // Host lowercased; path case preserved by normalizer (peerIdFromCanonical
  // lowercases the whole canonical before hashing/slugging).
  assert.equal(
    normalizeGitRemoteUrl("https://GitHub.Com/Volcengine/OpenViking"),
    "github.com/Volcengine/OpenViking",
  );
});

test("normalizeGitRemoteUrl: rejects local paths and empty input without throwing", () => {
  assert.equal(normalizeGitRemoteUrl("/abs/path/to/repo"), "");
  assert.equal(normalizeGitRemoteUrl("file:///abs/path/to/repo"), "");
  assert.equal(normalizeGitRemoteUrl("./relative/repo"), "");
  assert.equal(normalizeGitRemoteUrl(""), "");
  assert.equal(normalizeGitRemoteUrl(null), "");
  assert.equal(normalizeGitRemoteUrl(undefined), "");
  assert.equal(normalizeGitRemoteUrl(123), "");
});

// ----- peerIdFromCanonical (pure) ----------------------------------------

test("peerIdFromCanonical: deterministic, single-segment, lowercase only", () => {
  const id = peerIdFromCanonical("github.com/volcengine/openviking");
  assert.match(id, /^git-[a-z0-9-]+-[0-9a-f]{8}$/);
  assert.equal(id, peerIdFromCanonical("github.com/volcengine/openviking"));
});

test("peerIdFromCanonical: SSH and HTTPS forms of the same repo yield the same peer id", () => {
  // Both normalizer outputs feed into peerIdFromCanonical; after lowercasing
  // they must collide.
  const a = peerIdFromCanonical(normalizeGitRemoteUrl("git@github.com:Volcengine/OpenViking.git"));
  const b = peerIdFromCanonical(normalizeGitRemoteUrl("https://github.com/volcengine/openviking"));
  assert.equal(a, b);
});

test("peerIdFromCanonical: credentials never appear in the peer id", () => {
  const sensitive = peerIdFromCanonical(
    normalizeGitRemoteUrl("https://alice:s3cret-token@github.com/owner/repo.git"),
  );
  assert.ok(!sensitive.includes("alice"));
  assert.ok(!sensitive.includes("s3cret"));
  assert.ok(!sensitive.includes("token"));
  assert.match(sensitive, /^git-[a-z0-9-]+-[0-9a-f]{8}$/);
});

test("peerIdFromCanonical: caps long slugs but keeps the disambiguating hash", () => {
  const longCanonical = "github.com/" + "a".repeat(80) + "/repo";
  const id = peerIdFromCanonical(longCanonical);
  assert.match(id, /^git-([a-z0-9-]{1,48})-[0-9a-f]{8}$/);
  // different long inputs must not collide on slug alone — the hash differs.
  const other = peerIdFromCanonical("github.com/" + "b".repeat(80) + "/repo");
  assert.notEqual(id, other);
});

test("peerIdFromCanonical: empty input yields empty string", () => {
  assert.equal(peerIdFromCanonical(""), "");
  assert.equal(peerIdFromCanonical(null), "");
});

// ----- resolveEffectivePeerId (contract) ---------------------------------

test("resolveEffectivePeerId: explicit override wins over everything", () => {
  assert.deepEqual(
    resolveEffectivePeerId({ cfg: { peerId: " configured " }, cwd: "/tmp/project" }),
    { peerId: "configured", source: "explicit" },
  );
});

test("resolveEffectivePeerId: workspacePeer=false disables derivation", () => {
  assert.deepEqual(
    resolveEffectivePeerId({ cfg: { workspacePeer: false }, cwd: "/tmp/project" }),
    { peerId: "", source: "none" },
  );
});

test("resolveEffectivePeerId: empty cwd yields no peer", () => {
  assert.deepEqual(resolveEffectivePeerId({ cfg: {}, cwd: "" }), { peerId: "", source: "none" });
});

// ----- deriveWorkspacePeerId fallbacks (no real repo) --------------------

test("deriveWorkspacePeerId: non-git directory falls back to absolute path slug", () => {
  // /tmp subpath that does not exist as a git repo → legacy slug.
  assert.equal(
    deriveWorkspacePeerId("/tmp/definitely-not-a-git-repo-xyz/OpenViking"),
    "-tmp-definitely-not-a-git-repo-xyz-OpenViking",
  );
});

test("deriveWorkspacePeerId: empty cwd yields empty string", () => {
  assert.equal(deriveWorkspacePeerId(""), "");
  assert.equal(deriveWorkspacePeerId(null), "");
});

// ----- integration: real temp git repos ----------------------------------

async function makeTempDir(prefix) {
  return mkdtemp(join(tmpdir(), `wspeer-${prefix}-`));
}

function gitInit(cwd, remoteUrl) {
  execFileSync("git", ["init", "-q", cwd], { stdio: "ignore" });
  if (remoteUrl) {
    execFileSync("git", ["-C", cwd, "remote", "add", "origin", remoteUrl], { stdio: "ignore" });
  }
  // Make the repo usable (some git versions need an initial commit for
  // rev-parse --show-toplevel to behave in worktrees).
  execFileSync("git", ["-C", cwd, "config", "user.email", "t@t"], { stdio: "ignore" });
  execFileSync("git", ["-C", cwd, "config", "user.name", "t"], { stdio: "ignore" });
  execFileSync("git", ["-C", cwd, "add", "-A"], { stdio: "ignore" });
  try {
    execFileSync("git", ["-C", cwd, "commit", "-q", "-m", "init", "--allow-empty"], { stdio: "ignore" });
  } catch {
    // ignore commit hook failures
  }
}

test("integration: git repo with origin derives git-prefixed peer", { skip: !HAS_GIT }, async () => {
  const repo = await makeTempDir("origin");
  try {
    gitInit(repo, "git@github.com:volcengine/OpenViking.git");
    const id = deriveWorkspacePeerId(repo);
    const expected = peerIdFromCanonical("github.com/volcengine/openviking");
    assert.equal(id, expected);
    assert.deepEqual(
      resolveEffectivePeerId({ cfg: {}, cwd: repo }),
      { peerId: expected, source: "workspace" },
    );
  } finally {
    await rm(repo, { recursive: true, force: true });
  }
});

test("integration: SSH and HTTPS remotes resolve to the same peer", { skip: !HAS_GIT }, async () => {
  const sshRepo = await makeTempDir("ssh");
  const httpsRepo = await makeTempDir("https");
  try {
    gitInit(sshRepo, "git@github.com:volcengine/OpenViking.git");
    gitInit(httpsRepo, "https://github.com/volcengine/OpenViking.git");
    assert.equal(deriveWorkspacePeerId(sshRepo), deriveWorkspacePeerId(httpsRepo));
  } finally {
    await Promise.all([rm(sshRepo, { recursive: true, force: true }), rm(httpsRepo, { recursive: true, force: true })]);
  }
});

test("integration: credentials in remote URL do not leak into peer id", { skip: !HAS_GIT }, async () => {
  const repo = await makeTempDir("creds");
  try {
    gitInit(repo, "https://alice:hunter2-token@github.com/volcengine/OpenViking.git");
    const id = deriveWorkspacePeerId(repo);
    assert.ok(!id.includes("alice"));
    assert.ok(!id.includes("hunter2"));
    assert.ok(!id.includes("token"));
    assert.equal(id, peerIdFromCanonical("github.com/volcengine/openviking"));
  } finally {
    await rm(repo, { recursive: true, force: true });
  }
});

test("integration: linked worktree shares peer with main checkout", { skip: !HAS_GIT }, async () => {
  const mainRepo = await makeTempDir("main");
  try {
    gitInit(mainRepo, "https://github.com/volcengine/OpenViking.git");
    const wt = join(mainRepo, "..", `wt-${Date.now()}`);
    execFileSync(
      "git",
      ["-C", mainRepo, "worktree", "add", "-q", "--detach", wt],
      { stdio: "ignore" },
    );
    try {
      const mainId = deriveWorkspacePeerId(mainRepo);
      const wtId = deriveWorkspacePeerId(wt);
      assert.equal(mainId, wtId);
      assert.match(mainId, /^git-[a-z0-9-]+-[0-9a-f]{8}$/);
    } finally {
      try {
        execFileSync("git", ["-C", mainRepo, "worktree", "remove", "--force", wt], { stdio: "ignore" });
      } catch {
        /* ignore */
      }
    }
  } finally {
    await rm(mainRepo, { recursive: true, force: true });
  }
});

test("integration: git repo without remote uses stable local-repo identity", { skip: !HAS_GIT }, async () => {
  const repo = await makeTempDir("noremove");
  try {
    gitInit(repo, null);
    const id = deriveWorkspacePeerId(repo);
    assert.match(id, /^git-local-[0-9a-f]{12}$/);
    // Stable across subdirs of the same clone.
    await mkdir(join(repo, "sub"));
    assert.equal(deriveWorkspacePeerId(join(repo, "sub")), id);
  } finally {
    await rm(repo, { recursive: true, force: true });
  }
});

test("integration: main checkout and a linked worktree without remote share local peer", { skip: !HAS_GIT }, async () => {
  const mainRepo = await makeTempDir("noremotewt");
  try {
    gitInit(mainRepo, null);
    const wt = join(mainRepo, "..", `wt2-${Date.now()}`);
    execFileSync("git", ["-C", mainRepo, "worktree", "add", "-q", "--detach", wt], { stdio: "ignore" });
    try {
      const mainId = deriveWorkspacePeerId(mainRepo);
      const wtId = deriveWorkspacePeerId(wt);
      assert.equal(mainId, wtId);
      assert.match(mainId, /^git-local-[0-9a-f]{12}$/);
    } finally {
      try {
        execFileSync("git", ["-C", mainRepo, "worktree", "remove", "--force", wt], { stdio: "ignore" });
      } catch {
        /* ignore */
      }
    }
  } finally {
    await rm(mainRepo, { recursive: true, force: true });
  }
});


// ---------------------------------------------------------------------------
// Adversarial-review regression: non-default port + "@" in path (issue #3516).
// ---------------------------------------------------------------------------

test("normalizeGitRemoteUrl: non-default port collapses with default port", () => {
  // Enterprise GitLab/Gitea commonly run ssh on 2222 / 3000. Same repo, different
  // port config must NOT split peers.
  const a = normalizeGitRemoteUrl("ssh://git@gitlab.mycompany.com:2222/team/repo.git");
  const b = normalizeGitRemoteUrl("git@gitlab.mycompany.com:team/repo.git");
  assert.equal(a, b);
  assert.equal(a, "gitlab.mycompany.com/team/repo");
});

test("normalizeGitRemoteUrl: https with non-default https port collapses", () => {
  const a = normalizeGitRemoteUrl("https://git.example.com:8443/org/repo.git");
  const b = normalizeGitRemoteUrl("https://git.example.com/org/repo.git");
  assert.equal(a, b);
  assert.equal(a, "git.example.com/org/repo");
});

test("normalizeGitRemoteUrl: @ in path is not treated as userinfo", () => {
  // A ref/path containing "@" must not be mistaken for user@host separation.
  // https://github.com/@scope/repo must keep host "github.com" (not drop to "scope/repo").
  const out = normalizeGitRemoteUrl("https://github.com/@scope/repo.git");
  assert.equal(out, "github.com/@scope/repo");
  // And it must NOT collide across hosts.
  const other = normalizeGitRemoteUrl("https://gitlab.com/@scope/repo.git");
  assert.notEqual(out, other);
});

test("normalizeGitRemoteUrl: trailing @v1.0 ref does not corrupt the host", () => {
  const out = normalizeGitRemoteUrl("https://github.com/org/repo.git/@v1.0");
  // host is preserved; the @ref stays in path (silently, no credential leak)
  assert.ok(out.startsWith("github.com/org/repo"), out);
  assert.ok(!out.includes("user:pass"), out);
});
