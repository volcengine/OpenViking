import assert from "node:assert/strict";
import test from "node:test";

import { HOSTS } from "../hosts/index.mjs";
import { CLIENTS } from "../scripts/ov-memory-doctor.mjs";

// The doctor falls back to "cursor" for any client name it does not know
// (resolveClient), so a host added to hosts/index.mjs without a CLIENTS entry
// makes the host's own doctor report against Cursor's paths and fail a healthy
// install — the installer even prints the doctor as the host's Verify command.
// Kimi Code is the documented exception: its managed bundle is not
// config-driven and carries its own doctor invocation.
test("the memory doctor knows every config-driven host the registry serves", () => {
  const configDriven = Object.keys(HOSTS).filter((host) => host !== "kimicode").sort();
  assert.deepEqual(Object.keys(CLIENTS).sort(), configDriven);
  for (const [name, spec] of Object.entries(CLIENTS)) {
    assert.equal(typeof spec.hooks, "function", `${name} must know its hooks path`);
    assert.equal(typeof spec.launcherHint, "string", `${name} must know its launcher hint`);
  }
});
