import assert from "node:assert/strict";
import test from "node:test";
import { injectStartupProfile } from "./lifecycle.mjs";

test("startup injection leaves profile ownership to pre-step after a turn starts", async () => {
  let release;
  const initialized = new Promise(resolve => {
    release = resolve;
  });
  const injected = [];
  const agent = {
    status: "idle",
    inject(message) {
      injected.push(message);
    },
  };
  let claimed = 0;
  const runtime = {
    async initialize() {
      await initialized;
    },
    async profileMessage() {
      claimed += 1;
      return { profile: true };
    },
  };

  const pending = injectStartupProfile(agent, runtime);
  agent.status = "running";
  release();

  assert.equal(await pending, false);
  assert.equal(claimed, 0);
  assert.deepEqual(injected, []);
});

test("startup injection claims and injects a profile while the agent remains idle", async () => {
  const injected = [];
  const agent = {
    status: "idle",
    inject(message) {
      injected.push(message);
    },
  };
  const runtime = {
    async initialize() {},
    async profileMessage() {
      return { profile: true };
    },
  };

  assert.equal(await injectStartupProfile(agent, runtime), true);
  assert.deepEqual(injected, [{ profile: true }]);
});
