import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { hostOptions } from "../lib/host-options.mjs";

const execute = promisify(execFile);
const root = fileURLToPath(new URL("../../", import.meta.url));
async function fixture(t) {
  const directory = await fs.mkdtemp(join(tmpdir(), "ov-host-options-"));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const registry = join(directory, "magpie", "plugins.json");
  await fs.mkdir(join(directory, "magpie"));
  return { directory, registry, write: (plugins) => fs.writeFile(registry, JSON.stringify({ plugins })) };
}
test("recovery selects this installation's Options, including disabled entries", async (t) => {
  const f = await fixture(t);
  const options = { serverUrl: "http://127.0.0.1:8020", rootApiKeyFile: "/private/root", botMode: "independent", ovcliConfig: "/private/client" };
  await f.write([{ spec: "unrelated", options: { rootApiKeyFile: "wrong" } }, { spec: "@openviking/magpie-plugin@latest", options: {} }, { spec: root, off: true, options }]);
  assert.deepEqual(await hostOptions({ XDG_CONFIG_HOME: f.directory }), options);
  await f.write([{ spec: "@openviking/magpie-plugin@0.2.0", options }]);
  assert.deepEqual(await hostOptions({}, f.registry), options);
  await f.write([{ spec: root }, { spec: root }]);
  await assert.rejects(hostOptions({}, f.registry), /ambiguous_magpie_plugin_options/);
  assert.deepEqual(await hostOptions({}, join(f.directory, "missing")), {});
});
test("actual recovery CLI authenticates using Root key configured only in Magpie Options", async (t) => {
  const f = await fixture(t);
  let authenticated = 0;
  const server = createServer((req, res) => {
    if (req.headers["x-api-key"] !== "private-cli-root") { res.writeHead(401).end(); return; }
    authenticated++;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ status: "ok", result: { revision: "7", restart_required: false } }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const keyFile = join(f.directory, "root-key");
  const cliFile = join(f.directory, "ovcli.conf");
  await fs.writeFile(keyFile, "private-cli-root", { mode: 0o600 });
  await fs.writeFile(cliFile, JSON.stringify({ url: "http://127.0.0.1:1" }));
  await f.write([{ spec: root, options: { serverUrl: `http://127.0.0.1:${server.address().port}`, rootApiKeyFile: keyFile, ovcliConfig: cliFile } }]);
  await fs.writeFile(join(f.directory, "status.json"), JSON.stringify({ status: "saved", revision: "7", ovRuntime: "restart_required", botRuntime: "not_managed" }));
  const env = { ...process.env, PATH: "", OPENVIKING_MAGPIE_MODELS_DIR: f.directory, OPENVIKING_CLI_CONFIG_FILE: cliFile };
  delete env.OPENVIKING_ROOT_API_KEY;
  for (const args of [["apply"], ["apply", "--magpie-config", join(f.directory, "magpie")]]) {
    const { stdout, stderr } = await execute(process.execPath, [join(root, "models/cli.mjs"), ...args], { env: { ...env, XDG_CONFIG_HOME: f.directory } });
    assert.equal(JSON.parse(stdout).status, "applied");
    assert.doesNotMatch(stdout + stderr, /private-cli-root/);
  }
  assert.equal(authenticated, 2);
});
