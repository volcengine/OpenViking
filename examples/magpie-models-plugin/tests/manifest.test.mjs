import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { settings } from "../lib/config.mjs";
test("only the Agent is declared and automatic activation defaults on", async () => {
  const manifest = JSON.parse(await fs.readFile(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(manifest.magpie.page, undefined);
  assert.equal(manifest.magpie.panels, undefined);
  assert.equal(manifest.magpie.agent, "./openviking.agent.js");
  assert.equal(manifest.magpie.options.restartOnChange, true);
  const directory = await fs.mkdtemp(join(tmpdir(), "ov-model-default-"));
  try {
    const cli = join(directory, "ovcli.conf"); await fs.writeFile(cli, JSON.stringify({ url: "http://127.0.0.1:1933" }));
    const env = { OPENVIKING_MAGPIE_MODELS_DIR: directory, OPENVIKING_CLI_CONFIG_FILE: cli, PATH: "" };
    assert.equal((await settings({}, env)).restartOnChange, true);
    assert.equal((await settings({ restartOnChange: false }, env)).restartOnChange, false);
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});
