import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { startAdapter } from "../models/lib/runtime.mjs";
import { pluginEnabled } from "../config.mjs";

test("global disable stops background models before connection discovery and can be changed live", async (t) => {
  const directory = await fs.mkdtemp(join(tmpdir(), "ov-global-enable-"));
  const cliFile = join(directory, "ovcli.conf");
  const previous = process.env.OPENVIKING_CLI_CONFIG_FILE;
  process.env.OPENVIKING_CLI_CONFIG_FILE = cliFile;
  t.after(async () => {
    if (previous === undefined) delete process.env.OPENVIKING_CLI_CONFIG_FILE;
    else process.env.OPENVIKING_CLI_CONFIG_FILE = previous;
    await fs.rm(directory, { recursive: true, force: true });
  });
  const write = (enabled) => fs.writeFile(cliFile, JSON.stringify({ url: "http://127.0.0.1:1933", plugin: { enabled } }));
  await write(false);
  const logs = [];
  let optionReads = 0;
  const options = { get serverUrl() { optionReads++; throw new Error("must not discover connections while disabled"); } };
  const stop = startAdapter({ client: { app: { log: ({ body }) => logs.push(body.message) } } }, options);
  t.after(stop);
  const waitFor = async (fn) => {
    const deadline = Date.now() + 5000;
    while (!fn() && Date.now() < deadline) await delay(20);
    assert.ok(fn());
  };
  await waitFor(() => logs.some((message) => message.endsWith("disabled")));
  assert.equal(optionReads, 0);
  assert.equal(pluginEnabled(), false);
  await write(true);
  await waitFor(() => optionReads > 0);
  await write(false);
  await waitFor(() => logs.at(-1).endsWith("disabled"));
  const reads = optionReads;
  await delay(2100);
  assert.equal(optionReads, reads);
  stop();
  await fs.rm(cliFile);
  assert.equal(pluginEnabled(), true, "models-only Options work without a memory client file");
});
