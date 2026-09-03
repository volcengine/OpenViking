import test from "node:test";
import assert from "node:assert/strict";

import { assessHooksFeature, parseFeaturesList } from "./ov-memory-doctor.mjs";

test("parseFeaturesList: parses standard codex features list output", () => {
  const sample = `
hooks                                    stable             true
plugin_hooks                             removed            false
plugins                                  stable             true
apply_patch_preserve_line_endings        under development  false
`;
  const map = parseFeaturesList(sample);
  assert.equal(map.size, 4);
  assert.deepEqual(map.get("hooks"), { stage: "stable", enabled: true });
  assert.deepEqual(map.get("plugin_hooks"), { stage: "removed", enabled: false });
  assert.deepEqual(map.get("plugins"), { stage: "stable", enabled: true });
  assert.deepEqual(map.get("apply_patch_preserve_line_endings"), { stage: "under development", enabled: false });
});

test("assessHooksFeature: modern Codex with explicit hooks = true in toml", () => {
  const res = assessHooksFeature({ hooks: true });
  assert.equal(res.status, "ok");
  assert.equal(res.message, "[features] hooks = true");
});

test("assessHooksFeature: legacy Codex with explicit plugin_hooks = true in toml", () => {
  const res = assessHooksFeature({ plugin_hooks: true });
  assert.equal(res.status, "ok");
  assert.match(res.message, /\[features\] plugin_hooks = true \(legacy/);
});

test("assessHooksFeature: both hooks = true and plugin_hooks = true in toml", () => {
  const res = assessHooksFeature({ hooks: true, plugin_hooks: true });
  assert.equal(res.status, "ok");
  assert.equal(res.message, "[features] hooks = true");
});

test("assessHooksFeature: hooks explicitly false in toml", () => {
  const res = assessHooksFeature({ hooks: false });
  assert.equal(res.status, "fail");
  assert.match(res.message, /hooks disabled in \[features\]/);
  assert.match(res.fix, /set hooks = true/);
});

test("assessHooksFeature: plugin_hooks explicitly false in toml", () => {
  const res = assessHooksFeature({ plugin_hooks: false });
  assert.equal(res.status, "fail");
  assert.match(res.message, /hooks disabled in \[features\]/);
});

test("assessHooksFeature: unset in toml, but CLI probe reports hooks enabled (stable default)", () => {
  const cliFeatures = new Map([["hooks", { stage: "stable", enabled: true }]]);
  const res = assessHooksFeature({}, cliFeatures);
  assert.equal(res.status, "ok");
  assert.match(res.message, /hooks enabled by default \(Codex stage: stable\)/);
});

test("assessHooksFeature: unset in toml, but CLI probe reports hooks disabled", () => {
  const cliFeatures = new Map([["hooks", { stage: "under development", enabled: false }]]);
  const res = assessHooksFeature({}, cliFeatures);
  assert.equal(res.status, "fail");
  assert.match(res.message, /hooks feature is disabled in Codex/);
});

test("assessHooksFeature: unset in toml and CLI probe unavailable (fallback to info)", () => {
  const resEmpty = assessHooksFeature({}, null);
  assert.equal(resEmpty.status, "info");
  assert.match(resEmpty.message, /\[features\] hooks is not set/);
  assert.match(resEmpty.fix, /hooks = true/);

  const resUndef = assessHooksFeature(undefined, null);
  assert.equal(resUndef.status, "info");
  assert.match(resUndef.message, /\[features\] hooks is not set/);
});
