import assert from "node:assert/strict";
import { test } from "node:test";
import { deriveDshSessionId, historySessionIds } from "./session-id.mjs";

test("safe DSH session ids keep their existing OpenViking identity", () => {
  assert.equal(deriveDshSessionId("web-session_01"), "dsh-web-session_01");
});

test("Windows-unsafe IM session ids become portable and deterministic", () => {
  const native = "im:weixin_fixture:dm:1790750070550:chat_fixture@im.wechat";
  const expected = "dsh-im_weixin_fixture_dm_1790750070550_chat_fixture@im.wechat__eb8b3dae7d74";

  assert.equal(deriveDshSessionId(native), expected);
  assert.equal(deriveDshSessionId(native), expected);
  assert.doesNotMatch(expected, /[<>:"/\\|?*\u0000-\u001F]/);
});

test("normalization cannot merge distinct native session ids", () => {
  const colon = deriveDshSessionId("im:a");
  const question = deriveDshSessionId("im?a");

  assert.equal(colon, "dsh-im_a__2ffb1edac2e4");
  assert.equal(question, "dsh-im_a__c0def8231117");
  assert.notEqual(colon, question);
});

test("trailing dots and spaces are removed from the path component", () => {
  assert.equal(deriveDshSessionId("name."), "dsh-name___c39208ad84ba");
  assert.doesNotMatch(deriveDshSessionId("name "), /[. ]$/);
});

test("a seeded session's history continues through its parent chain", () => {
  const sessions = new Map([
    ["fork", { header: { id: "fork", parentSession: "main", isSeeded: true } }],
    ["main", { header: { id: "main", isSeeded: false } }],
  ]);
  const sub = { id: "sub", header: { parentSession: "fork", isSeeded: true, origin: "subagent" } };
  assert.equal(historySessionIds(sub, sessions), "dsh-sub,dsh-fork,dsh-main");
  assert.equal(historySessionIds(sub), "dsh-sub,dsh-fork");
  assert.equal(historySessionIds({ id: "fresh", header: { parentSession: "main", isSeeded: false } }), "dsh-fresh");
  assert.equal(historySessionIds({ id: "solo", header: {} }), "dsh-solo");

  const loop = new Map([["a", { header: { parentSession: "a", isSeeded: true } }]]);
  assert.equal(historySessionIds({ id: "a", header: loop.get("a").header }, loop).split(",").length, 10);
});
