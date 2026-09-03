import test from "node:test";
import assert from "node:assert/strict";

import { assessHooksFeature } from "./ov-memory-doctor.mjs";

test("assessHooksFeature: modern Codex with hooks = true", () => {
  const res = assessHooksFeature({ hooks: true });
  assert.equal(res.ok, true);
  assert.equal(res.message, "[features] hooks = true");
});

test("assessHooksFeature: legacy Codex with plugin_hooks = true", () => {
  const res = assessHooksFeature({ plugin_hooks: true });
  assert.equal(res.ok, true);
  assert.match(res.message, /\[features\] plugin_hooks = true \(legacy/);
});

test("assessHooksFeature: both hooks = true and plugin_hooks = true", () => {
  const res = assessHooksFeature({ hooks: true, plugin_hooks: true });
  assert.equal(res.ok, true);
  assert.equal(res.message, "[features] hooks = true");
});

test("assessHooksFeature: hooks explicitly false", () => {
  const res = assessHooksFeature({ hooks: false });
  assert.equal(res.ok, false);
  assert.match(res.message, /hooks disabled in \[features\]/);
  assert.match(res.fix, /set hooks = true/);
});

test("assessHooksFeature: plugin_hooks explicitly false", () => {
  const res = assessHooksFeature({ plugin_hooks: false });
  assert.equal(res.ok, false);
  assert.match(res.message, /hooks disabled in \[features\]/);
});

test("assessHooksFeature: neither set (empty object or undefined)", () => {
  const resEmpty = assessHooksFeature({});
  assert.equal(resEmpty.ok, false);
  assert.equal(resEmpty.message, "[features] hooks is not set");
  assert.match(resEmpty.fix, /hooks = true/);
  assert.match(resEmpty.fix, /plugin_hooks = true for older Codex/);

  const resUndef = assessHooksFeature(undefined);
  assert.equal(resUndef.ok, false);
  assert.equal(resUndef.message, "[features] hooks is not set");
});
