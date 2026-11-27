import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { SERVER_FEATURES, featureStatus, healthVersion, requiredVersion, supports } from "../agent-hook-plugin/lib/server-features.mjs";

// The gateway's test suite reads the same cases, so the two readers are proven
// to answer alike for every string in it.
const CASES = JSON.parse(
  readFileSync(new URL("../../tests/fixtures/server_feature_versions.json", import.meta.url), "utf-8"),
);

test("the table is the source file", () => {
  const source = readFileSync(new URL("../agent-hook-plugin/lib/server-features.json", import.meta.url), "utf-8");
  assert.deepEqual(SERVER_FEATURES, JSON.parse(source));
  assert.ok(Object.isFrozen(SERVER_FEATURES));
  assert.equal(requiredVersion("working_memory_opt_in"), "0.5.0");
});

test("supports agrees with the shared version cases", () => {
  assert.ok(CASES.length > 0);
  for (const { feature, version, supported } of CASES) {
    assert.equal(supports(feature, version), supported, `supports(${JSON.stringify(feature)}, ${JSON.stringify(version)})`);
  }
});

test("a version that cannot be read supports nothing", () => {
  for (const version of [undefined, {}, ["0.5.0"]]) assert.equal(supports("working_memory_opt_in", version), false);
});

test("an unknown feature name is a programming error", () => {
  assert.throws(() => supports("time_travel", "9.9.9"), /unknown server feature: time_travel/);
  assert.throws(() => requiredVersion("toString"), /unknown server feature/);
});

test("featureStatus carries what a message needs", () => {
  assert.deepEqual(featureStatus("working_memory_opt_in", "0.4.23"), {
    feature: "working_memory_opt_in",
    supported: false,
    required: "0.5.0",
    actual: "0.4.23",
  });
  assert.deepEqual(featureStatus("working_memory_opt_in", null), {
    feature: "working_memory_opt_in",
    supported: false,
    required: "0.5.0",
    actual: null,
  });
});

test("healthVersion reads /health's version and is null when the check fails", async () => {
  const paths = [];
  const ok = async (path) => {
    paths.push(path);
    return { ok: true, status: 200, result: { status: "ok", healthy: true, version: "0.5.7.dev44" } };
  };
  assert.equal(await healthVersion(ok), "0.5.7.dev44");
  assert.deepEqual(paths, ["/health"]);
  assert.equal(await healthVersion(async () => ({ ok: false, status: 503, result: null })), null);
  assert.equal(await healthVersion(async () => ({ ok: true, status: 200, result: { healthy: true } })), null);
  assert.equal(await healthVersion(async () => { throw new Error("down"); }), null);
});
