import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  PEER_SOURCE_PRESETS,
  deriveWorkspacePeerId,
  peerSourceTemplates,
  renderPeerTemplate,
  resolveEffectivePeerId,
} from "./lib/workspace-peer.mjs";
import { resolveWorkspaceIdentity } from "./lib/workspace-identity.mjs";

async function repo({ remote = "git@github.com:volcengine/OpenViking.git", git = true, marked = false } = {}) {
  const root = realpathSync(await mkdtemp(join(tmpdir(), "ov-peer-")));
  if (git) {
    await mkdir(join(root, ".git"), { recursive: true });
    await writeFile(
      join(root, ".git", "config"),
      remote ? `[remote "origin"]\n\turl = ${remote}\n` : "[core]\n\trepositoryformatversion = 0\n",
    );
  }
  if (marked) {
    await mkdir(join(root, ".openviking"), { recursive: true });
    await writeFile(join(root, ".openviking", "config.json"), '{"version":1}\n');
  }
  const env = { HOME: "/nonexistent-home", OPENVIKING_STATE_DIR: join(root, ".state") };
  return { root, env };
}

function resolve(cwd, env, cfg = {}) {
  return resolveEffectivePeerId({ cfg, cwd, identity: resolveWorkspaceIdentity({ cwd, env, cache: false }), env });
}

test("deriveWorkspacePeerId keeps the byte-for-byte legacy rule", () => {
  assert.equal(deriveWorkspacePeerId("/Users/x/Dev/OpenViking"), "-Users-x-Dev-OpenViking");
  assert.equal(deriveWorkspacePeerId("/tmp/a  b/"), "-tmp-a--b-");
  assert.equal(deriveWorkspacePeerId("abc.DEF_123@x-y"), "abc-DEF-123-x-y");
  assert.equal(deriveWorkspacePeerId(""), "");
  assert.equal(deriveWorkspacePeerId(null), "");
});

test("an explicit peer id still wins over every rule", async () => {
  const { root, env } = await repo();
  assert.deepEqual(resolve(root, env, { peerId: " configured " }), {
    peerId: "configured",
    source: "explicit",
    origin: "explicit",
    legacyPeerId: "",
  });
});

test("the default is the repository, from any subdirectory or clone", async () => {
  const { root, env } = await repo();
  const deep = join(root, "examples", "codex-memory-plugin");
  await mkdir(deep, { recursive: true });

  const top = resolve(root, env);
  const nested = resolve(deep, env);
  assert.equal(top.peerId, "github.com-volcengine-openviking");
  assert.equal(nested.peerId, top.peerId, "a subdirectory is the same workspace");
  assert.equal(top.source, "workspace", "call sites compare this against the literal");
  assert.equal(top.origin, "{git_remote}");
  assert.equal(nested.legacyPeerId, deriveWorkspacePeerId(deep), "the pre-git id stays reachable");
});

test("the git preset falls back to the repository root, and stops there", async () => {
  const noRemote = await repo({ remote: "" });
  const rootDerived = resolve(noRemote.root, noRemote.env);
  assert.equal(rootDerived.peerId, deriveWorkspacePeerId(noRemote.root));
  assert.equal(rootDerived.origin, "{git_root}");
  assert.equal(rootDerived.legacyPeerId, "", "nothing to fall back to when it already is the legacy id");

  // A directory that is no workspace gets no peer — an app that creates a
  // fresh directory per task must not mint a fresh peer per task. The id the
  // old rule would have used is still reported, so recall under `actor`
  // scope keeps reaching what earlier sessions wrote there.
  const plain = await repo({ git: false });
  const deep = join(plain.root, "outputs");
  await mkdir(deep, { recursive: true });
  const notAWorkspace = resolve(deep, plain.env);
  assert.deepEqual(notAWorkspace, {
    peerId: "",
    source: "none",
    origin: "unresolved",
    legacyPeerId: deriveWorkspacePeerId(deep),
  });
});

test("marking a plain directory makes it a workspace, but naming its peer is a separate step", async () => {
  const marked = await repo({ git: false, marked: true });
  const deep = join(marked.root, "src");
  await mkdir(deep, { recursive: true });

  // The marker is what makes `.openviking/config.json` apply from any depth;
  // it does not by itself derive a peer, because the default chain is git-only.
  assert.equal(resolve(deep, marked.env).origin, "unresolved");
  // What the file in it says does: an explicit id, or a template over the
  // workspace root's name.
  assert.equal(resolve(deep, marked.env, { peerId: "my-project" }).peerId, "my-project");
  const named = resolve(deep, marked.env, { peerSource: "{dir}" });
  assert.equal(named.peerId, marked.root.split("/").pop(), "{dir} is the marked root, not the subdirectory");
  assert.equal(resolve(marked.root, marked.env, { peerSource: "{dir}" }).peerId, named.peerId);
});

test("the cwd preset reproduces the old identity exactly", async () => {
  const { root, env } = await repo();
  const deep = join(root, "examples");
  await mkdir(deep, { recursive: true });

  const legacy = resolve(deep, env, { peerSource: "cwd" });
  assert.equal(legacy.peerId, deriveWorkspacePeerId(deep));
  assert.equal(legacy.legacyPeerId, "");
});

test("none, and the switch that predates it, both send no peer", async () => {
  const { root, env } = await repo();
  assert.deepEqual(resolve(root, env, { peerSource: "none" }), {
    peerId: "", source: "none", origin: "none", legacyPeerId: "",
  });
  assert.deepEqual(resolve(root, env, { workspacePeer: false }), {
    peerId: "", source: "none", origin: "disabled", legacyPeerId: "",
  });
});

test("a template can shape the id, and a list is tried in order", async () => {
  const { root, env } = await repo();
  assert.equal(resolve(root, env, { peerSource: "git-{git_remote}" }).peerId, "git-github.com-volcengine-openviking");
  assert.equal(resolve(root, env, { peerSource: "team-{dir}" }).peerId, `team-${root.split("/").pop()}`);

  const noRemote = await repo({ remote: "" });
  const chain = resolve(noRemote.root, noRemote.env, { peerSource: ["{git_remote}", "team-{dir}"] });
  assert.equal(chain.peerId, `team-${noRemote.root.split("/").pop()}`, "an empty variable falls through");
  assert.equal(chain.origin, "team-{dir}");
});

test("{harness} splits one repository per agent, for whoever asks for it", async () => {
  const { root, env } = await repo();
  const template = "{git_remote}-{harness}";
  assert.equal(
    resolve(root, env, { peerSource: template, harness: "codex" }).peerId,
    "github.com-volcengine-openviking-codex",
  );
  assert.equal(
    resolve(root, env, { peerSource: template, clientId: "cursor" }).peerId,
    "github.com-volcengine-openviking-cursor",
    "the agent-hook harnesses carry the name as clientId",
  );
  assert.equal(
    resolve(root, env, { peerSource: template, harness: "Trae CN/2" }).peerId,
    "github.com-volcengine-openviking-Trae-CN-2",
    "a harness name is sanitized like every other variable",
  );

  // Without a harness the template is one empty variable, so it falls through
  // rather than handing back a peer with a dangling dash.
  const chain = resolve(root, env, { peerSource: [template, "{git_remote}"] });
  assert.equal(chain.peerId, "github.com-volcengine-openviking");
  assert.equal(chain.origin, "{git_remote}");
});

test("a template naming only empty variables resolves to no peer at all", async () => {
  const plain = await repo({ git: false });
  const unresolved = resolve(plain.root, plain.env, { peerSource: ["{git_remote}"] });
  assert.deepEqual(unresolved, {
    peerId: "", source: "none", origin: "unresolved", legacyPeerId: deriveWorkspacePeerId(plain.root),
  });
});

test("the cwd preset still gives a plain directory a peer, on request", async () => {
  const plain = await repo({ git: false });
  const optedIn = resolve(plain.root, plain.env, { peerSource: "cwd" });
  assert.equal(optedIn.peerId, deriveWorkspacePeerId(plain.root));
  assert.equal(optedIn.origin, "{cwd}");
});

test("renderPeerTemplate is all-or-nothing so no half-formed id escapes", () => {
  const vars = { git_remote: "github.com-o-r", git_root: "-src-r", cwd: "-src-r-sub", dir: "r" };
  assert.equal(renderPeerTemplate("{git_remote}", vars), "github.com-o-r");
  assert.equal(renderPeerTemplate("a-{dir}-b", vars), "a-r-b");
  assert.equal(renderPeerTemplate("{git_remote}-{missing}", vars), "");
  assert.equal(renderPeerTemplate("{git_remote}", { git_remote: "" }), "");
  assert.equal(renderPeerTemplate("literal", vars), "literal");
  assert.equal(renderPeerTemplate("", vars), "");
});

test("peerSourceTemplates resolves presets and passes templates through", () => {
  assert.deepEqual(PEER_SOURCE_PRESETS.git, ["{git_remote}", "{git_root}"], "no bare path in the default chain");
  assert.deepEqual(peerSourceTemplates(undefined), PEER_SOURCE_PRESETS.git);
  assert.deepEqual(peerSourceTemplates(""), PEER_SOURCE_PRESETS.git);
  assert.deepEqual(peerSourceTemplates("cwd"), ["{cwd}"]);
  assert.deepEqual(peerSourceTemplates("none"), []);
  assert.deepEqual(peerSourceTemplates("my-{dir}"), ["my-{dir}"]);
  assert.deepEqual(peerSourceTemplates(["{git_remote}", "{cwd}"]), ["{git_remote}", "{cwd}"]);
});

test("a misspelled preset warns and falls back instead of becoming the peer", () => {
  for (const typo of ["Git", "gti"]) {
    const warnings = [];
    assert.deepEqual(peerSourceTemplates(typo, (message) => warnings.push(message)), PEER_SOURCE_PRESETS.git);
    assert.equal(warnings.length, 1, `${typo} must be reported, not silently adopted`);
    assert.match(warnings[0], new RegExp(typo));
  }

  const quiet = [];
  const push = (message) => quiet.push(message);
  assert.deepEqual(peerSourceTemplates("team-{dir}", push), ["team-{dir}"], "a real template still passes through");
  assert.deepEqual(peerSourceTemplates(["release", "{cwd}"], push), ["release", "{cwd}"], "a list is explicit");
  assert.deepEqual(quiet, []);
});

test("a fork keeps its own peer, and every clone of one repo shares one", async () => {
  const upstream = await repo({ remote: "git@github.com:volcengine/OpenViking.git" });
  const fork = await repo({ remote: "git@github.com:t0saki/OpenViking.git" });
  const secondClone = await repo({ remote: "https://github.com/volcengine/OpenViking.git" });

  assert.equal(resolve(upstream.root, upstream.env).peerId, resolve(secondClone.root, secondClone.env).peerId);
  assert.notEqual(resolve(fork.root, fork.env).peerId, resolve(upstream.root, upstream.env).peerId);
});
