import assert from "node:assert/strict";
import test from "node:test";
import { guardVikingUri } from "./uri-guard.mjs";

test("uri guard blocks DSH filesystem tools from treating viking URIs as local paths", async () => {
  let delegated = false;
  const decision = await guardVikingUri({
    name: "read",
    arguments: { file_path: "viking://user/default/memories/profile.md" },
  }, async () => {
    delegated = true;
    return { kind: "allow" };
  });

  assert.equal(delegated, false);
  assert.equal(decision.kind, "deny");
  assert.match(decision.reason, /viking:\/\/ URIs are OpenViking virtual paths/);
  assert.match(decision.reason, /viking_read/);
});

test("uri guard delegates OpenViking tools and ordinary filesystem paths", async () => {
  const next = async () => ({ kind: "allow", marker: true });
  assert.deepEqual(
    await guardVikingUri({ name: "read", arguments: { file_path: "/tmp/a" } }, next),
    { kind: "allow", marker: true },
  );
  assert.deepEqual(
    await guardVikingUri({
      name: "viking_read",
      arguments: { uri: "viking://user/default/memories/profile.md" },
    }, next),
    { kind: "allow", marker: true },
  );
});
