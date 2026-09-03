import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { loadConfig } from "./config.mjs";

const ENV_KEYS = [
  "OPENVIKING_CONFIG_FILE",
  "OPENVIKING_CLI_CONFIG_FILE",
  "OPENVIKING_CREDENTIAL_SOURCE",
  "OPENVIKING_SCORE_THRESHOLD",
  "OPENVIKING_URL",
];

async function withIsolatedConfig(env, fn) {
  const previous = Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]));
  const dir = await mkdtemp(join(tmpdir(), "ov-codex-config-"));
  process.env.OPENVIKING_CONFIG_FILE = join(dir, "missing-ov.conf");
  process.env.OPENVIKING_CLI_CONFIG_FILE = join(dir, "missing-ovcli.conf");
  process.env.OPENVIKING_CREDENTIAL_SOURCE = "env";
  process.env.OPENVIKING_URL = "http://127.0.0.1:1933";
  for (const [key, value] of Object.entries(env)) process.env[key] = value;
  try {
    return await fn();
  } finally {
    for (const key of ENV_KEYS) {
      if (previous[key] === undefined) delete process.env[key];
      else process.env[key] = previous[key];
    }
  }
}

test("score threshold accepts negative reranker logits from the environment", async () => {
  await withIsolatedConfig({ OPENVIKING_SCORE_THRESHOLD: "-8" }, () => {
    assert.equal(loadConfig().scoreThreshold, -8);
  });
});

test("score threshold accepts positive reranker logits above one from the environment", async () => {
  await withIsolatedConfig({ OPENVIKING_SCORE_THRESHOLD: "4.5" }, () => {
    assert.equal(loadConfig().scoreThreshold, 4.5);
  });
});
