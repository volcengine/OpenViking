import assert from "node:assert/strict";
import test from "node:test";
import { resolveConfig } from "./config.mjs";

test("behavior environment overrides are applied and normalized", () => {
  const config = resolveConfig({}, {
    OPENVIKING_URL: "http://127.0.0.1:19464/",
    OPENVIKING_WORKSPACE_PEER: "0",
    OPENVIKING_RECALL_PEER_SCOPE: "actor",
    OPENVIKING_RECALL_QUERY_EXPANSION: "off",
    OPENVIKING_RECALL_LIMIT: "7",
  }, "/workspace/project");

  assert.equal(config.endpoint, "http://127.0.0.1:19464");
  assert.equal(config.workspacePeer, false);
  assert.equal(config.peerId, "");
  assert.equal(config.recallPeerScope, "actor");
  assert.equal(config.recallQueryExpansion, "off");
  assert.equal(config.recallQueryExpansionConfigured, true);
  assert.equal(config.recallLimit, 7);
  assert.equal(config.recallLimitConfigured, true);
});

test("explicit plugin config overrides credential files", () => {
  const config = resolveConfig({
    endpoint: "http://plugin.local",
    apiKey: "plugin-key",
    account: "plugin-account",
    user: "plugin-user",
    peerId: "plugin-peer",
  }, {
    OPENVIKING_URL: "http://env.local",
    OPENVIKING_API_KEY: "env-key",
    OPENVIKING_ACCOUNT: "env-account",
    OPENVIKING_USER: "env-user",
    OPENVIKING_PEER_ID: "env-peer",
  }, "/workspace/project");

  assert.equal(config.endpoint, "http://plugin.local");
  assert.equal(config.apiKey, "plugin-key");
  assert.equal(config.account, "plugin-account");
  assert.equal(config.user, "plugin-user");
  assert.equal(config.peerId, "plugin-peer");
});
