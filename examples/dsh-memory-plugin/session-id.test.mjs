import assert from "node:assert/strict";
import { test } from "node:test";
import { deriveDshSessionId } from "./session-id.mjs";

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
