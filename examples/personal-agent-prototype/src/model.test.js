import test from "node:test";
import assert from "node:assert/strict";
import { initialState, reduceState, suggestedPreference } from "./model.js";
const act = (s, type, rest = {}) =>
  reduceState(s, { type, goalId: "personal", ...rest });
const seed = () => {
  let s = initialState();
  s.goals.forEach((g) => (g.expiry = "2099-12-31"));
  return s;
};
test("only selected changes publish, twice cannot duplicate", () => {
  let s = seed();
  s = act(s, "SELECT", { id: "memory", value: false });
  s = act(s, "PUBLISH", { revision: s.revision });
  assert.equal(s.goals[0].version, 13);
  assert.equal(s.goals[0].patches[0].status, "published");
  assert.equal(s.goals[0].patches[1].status, "pending");
  const before = structuredClone(s);
  s = act(s, "PUBLISH", { revision: s.revision });
  assert.deepEqual(s, before);
});
test("memory correction blocks old draft, preserves published body, regenerates scoped draft", () => {
  let s = seed(),
    body = structuredClone(s.goals[0].document);
  s = act(s, "SAVE_MEMORY", {
    id: "range",
    text: suggestedPreference,
    scope: "personal",
  });
  assert(s.goals[0].stale);
  assert(!s.goals[1].stale);
  assert.deepEqual(s.goals[0].document, body);
  assert.equal(act(s, "PUBLISH", { revision: s.revision }), s);
  s = act(s, "RUN");
  s = act(s, "TICK", { now: Date.now() + 4000 });
  assert(!s.goals[0].stale);
  assert.match(s.goals[0].patches[2].after, /两个月/);
  assert.equal(s.memories[0].sync, "done");
});
test("concurrent edit and revoked grant block publishing", () => {
  let s = seed();
  s = act(s, "CONFLICT");
  assert.equal(act(s, "PUBLISH", { revision: s.revision }), s);
  s = act(s, "RUN");
  s = act(s, "TICK", { now: Date.now() + 4000 });
  s = act(s, "GRANT", { value: false });
  assert.equal(act(s, "PUBLISH", { revision: s.revision }), s);
});
test("approval cannot be reused after editing selected content", () => {
  let s = seed(),
    approval = s.revision;
  s = act(s, "EDIT_PATCH", { id: "scope", text: "new text" });
  assert.equal(act(s, "PUBLISH", { revision: approval }), s);
});
test("delete leaves original document and suppresses memory regeneration", () => {
  let s = seed(),
    body = structuredClone(s.goals[0].document);
  s = act(s, "MEMORY_STATUS", { id: "range", status: "deleted" });
  assert(!s.memories.some((m) => m.id === "range"));
  assert.equal(s.tombstones[0].id, "range");
  assert.deepEqual(s.goals[0].document, body);
  s = act(s, "RUN");
  s = act(s, "TICK", { now: Date.now() + 4000 });
  assert(!s.memories.some((m) => m.id === "range"));
  assert(!s.goals[0].patches[2].after.includes("仅纳入最近半年"));
});
test("cancelling running task cannot later replace the draft", () => {
  let s = seed(),
    before = structuredClone(s.goals[0].patches);
  s = act(s, "RUN");
  s = act(s, "CANCEL");
  s = act(s, "TICK", { now: Date.now() + 10000 });
  assert.deepEqual(s.goals[0].patches, before);
  assert(!s.goals[0].running);
});
test("global correction invalidates both goals", () => {
  let s = act(seed(), "SAVE_MEMORY", {
    id: "range",
    text: suggestedPreference,
    scope: "all",
  });
  assert(s.goals.every((g) => g.stale));
});
