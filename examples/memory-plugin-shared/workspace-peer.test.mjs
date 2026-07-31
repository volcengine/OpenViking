import test from "node:test";
import assert from "node:assert/strict";
import { deriveWorkspacePeerId, resolveEffectivePeerId } from "./lib/workspace-peer.mjs";

test("deriveWorkspacePeerId follows Claude project directory naming", () => {
  assert.equal(deriveWorkspacePeerId("/Users/x/Dev/OpenViking"), "-Users-x-Dev-OpenViking");
  assert.equal(deriveWorkspacePeerId("/tmp/a  b/"), "-tmp-a--b-");
  assert.equal(deriveWorkspacePeerId("abc.DEF_123@x-y"), "abc-DEF-123-x-y");
  assert.equal(deriveWorkspacePeerId(""), "");
  assert.equal(deriveWorkspacePeerId(null), "");
});

test("resolveEffectivePeerId prefers explicit peer over workspace", () => {
  assert.deepEqual(
    resolveEffectivePeerId({ cfg: { peerId: " configured " }, cwd: "/tmp/project" }),
    { peerId: "configured", source: "explicit" },
  );
});

test("resolveEffectivePeerId derives workspace peer by default", () => {
  assert.deepEqual(
    resolveEffectivePeerId({ cfg: {}, cwd: "/tmp/project" }),
    { peerId: "-tmp-project", source: "workspace" },
  );
});

test("resolveEffectivePeerId can disable workspace peer", () => {
  assert.deepEqual(
    resolveEffectivePeerId({ cfg: { workspacePeer: false }, cwd: "/tmp/project" }),
    { peerId: "", source: "none" },
  );
  assert.deepEqual(
    resolveEffectivePeerId({ cfg: {}, cwd: "" }),
    { peerId: "", source: "none" },
  );
});
