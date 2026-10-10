/**
 * Configuration tests.
 *
 * Two things are worth pinning. First, that the CodeBuddy connection resolves
 * from `~/.openviking/ovcli.conf` with no environment set — that file is the
 * plugin's config source on this machine, and a resolution that only worked with
 * OPENVIKING_URL in the environment would silently disable every hook for a
 * GUI-launched session. Second, that `plugin.codebuddy.*` overrides actually
 * apply: before the canonical harness key was registered, they resolved to a
 * silent no-op, which is the failure mode the plugin-development guide warns
 * about (and the reason the key had to be added rather than borrowed).
 */

import assert from "node:assert/strict";
import test from "node:test";

import { writeCredentialFiles } from "../../memory-plugin-shared/testing/support.mjs";
import { scrubOpenVikingEnv } from "../../memory-plugin-shared/testing/support.mjs";
import { isPluginEnabled, loadConfig } from "./config.mjs";

const restoreEnv = scrubOpenVikingEnv();
test.after(() => restoreEnv());

const URL = "http://10.0.0.1:1933";

/** Run `fn` with the process env pointed at a fresh ovcli.conf. */
async function withOvcli(ovcli, fn) {
  const { env } = await writeCredentialFiles("cb-config-", { ovcli });
  const saved = { ...process.env };
  try {
    for (const key of Object.keys(process.env)) if (key.startsWith("OPENVIKING_")) delete process.env[key];
    Object.assign(process.env, env, { OPENVIKING_MEMORY_ENABLED: "1" });
    return await fn();
  } finally {
    for (const key of Object.keys(process.env)) if (key.startsWith("OPENVIKING_")) delete process.env[key];
    Object.assign(process.env, saved);
  }
}

test("the connection resolves from ovcli.conf alone", async () => {
  await withOvcli({ url: URL, api_key: "k" }, () => {
    const cfg = loadConfig();
    assert.equal(cfg.baseUrl, URL);
    assert.equal(cfg.mcpUrl, `${URL}/mcp`);
    assert.equal(cfg.apiKeySource, "ovcli");
    assert.equal(cfg.authMode, "api_key");
    assert.equal(typeof cfg.timeoutMs, "number");
    assert.ok(cfg.timeoutMs > 0);
  });
});

test("plugin.codebuddy.* overrides apply", async () => {
  await withOvcli(
    { url: URL, api_key: "k", plugin: { codebuddy: { autoRecall: false, recallLimit: 3 } } },
    () => {
      const cfg = loadConfig();
      assert.equal(cfg.autoRecall, false);
      assert.equal(cfg.recallLimit, 3);
    },
  );
});

test("plugin.workbuddy.* does not override — the key must be codebuddy", async () => {
  await withOvcli(
    { url: URL, api_key: "k", plugin: { workbuddy: { autoRecall: false, recallLimit: 3 } } },
    () => {
      const cfg = loadConfig();
      assert.equal(cfg.autoRecall, true);
      assert.equal(cfg.recallLimit, 10);
    },
  );
});

test("isPluginEnabled honours the off switch", () => {
  const before = process.env.OPENVIKING_MEMORY_ENABLED;
  try {
    process.env.OPENVIKING_MEMORY_ENABLED = "0";
    assert.equal(isPluginEnabled(), false);
    process.env.OPENVIKING_MEMORY_ENABLED = "1";
    assert.equal(isPluginEnabled(), true);
  } finally {
    if (before === undefined) delete process.env.OPENVIKING_MEMORY_ENABLED;
    else process.env.OPENVIKING_MEMORY_ENABLED = before;
  }
});
