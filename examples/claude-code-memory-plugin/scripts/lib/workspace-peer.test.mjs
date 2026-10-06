import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";


const home = await mkdtemp(join(tmpdir(), "ov-claude-peer-pin-"));
process.env.OPENVIKING_HOME = home;

const { writeJsonState } = await import("./state.mjs");
const { getEffectivePeerId, PIN_VERSION } = await import("./workspace-peer.mjs");

test("the no-remote worktree derivation bump invalidates an older peer pin", async () => {
  assert.equal(PIN_VERSION, 4);
  writeJsonState("ws-peer-example.json", {
    version: 3,
    peerId: "linked-worktree-peer",
    source: "workspace",
    origin: "{git_root}",
  });

  const resolved = getEffectivePeerId(
    { peerSource: ["main-repository-peer"] },
    { sessionId: "example", cwd: home },
  );
  assert.equal(resolved.peerId, "main-repository-peer");

  const stored = JSON.parse(await readFile(join(home, "state", "ws-peer-example.json"), "utf-8"));
  assert.equal(stored.version, PIN_VERSION);
  assert.equal(stored.peerId, "main-repository-peer");
});

test.after(async () => { await rm(home, { recursive: true, force: true }); });
