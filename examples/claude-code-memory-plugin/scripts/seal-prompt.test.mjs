import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { serverOf } from "./seal-prompt.mjs";

const HELPER = join(dirname(fileURLToPath(import.meta.url)), "seal-prompt.mjs");

test("serverOf: the URL and the headers every other hook sends", () => {
  assert.deepEqual(serverOf({ baseUrl: "http://ov:1933/", apiKey: "sk-test" }, {}), {
    baseUrl: "http://ov:1933",
    headers: { "Content-Type": "application/json", Authorization: "Bearer sk-test" },
  });
  const extra = serverOf({ baseUrl: "http://ov" }, { OPENVIKING_EXTRA_HEADERS: '{"CF-Access-Client-Id":"id"}' });
  assert.equal(extra.headers["CF-Access-Client-Id"], "id");
  assert.equal(extra.headers.Authorization, undefined);
});

// The helper as the mod runs it: a process reading the plugin's config chain.
function runHelper(env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [HELPER], { env, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (c) => (stdout += c));
    child.stderr.on("data", (c) => (stderr += c));
    child.on("close", (code) => resolve({ code, stdout, stderr }));
    child.stdin.end();
  });
}

function isolatedEnv(dir, extra) {
  const env = { PATH: process.env.PATH, HOME: dir, OPENVIKING_HOME: join(dir, "home") };
  env.OPENVIKING_CONFIG_FILE = join(dir, "ov.conf");
  env.OPENVIKING_CLI_CONFIG_FILE = join(dir, "ovcli.conf");
  return { ...env, ...extra };
}

const readSetting = async (env) => {
  const res = await runHelper(env);
  assert.equal(res.code, 0, res.stderr);
  return JSON.parse(res.stdout);
};

test("the setting is off by default and follows ovcli.conf and the environment", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "cc-seal-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.deepEqual(await readSetting(isolatedEnv(dir, {})), { sealing: "off" });
  const forced = await readSetting(isolatedEnv(dir, { OPENVIKING_SEAL_SECRETS: "1", OPENVIKING_URL: "http://127.0.0.1:9" }));
  assert.equal(forced.sealing, "on");
  assert.equal(forced.server.baseUrl, "http://127.0.0.1:9");

  writeFileSync(
    join(dir, "ovcli.conf"),
    JSON.stringify({ url: "http://x:1933", api_key: "sk-test", plugin: { claude_code: { sealSecrets: true } } }),
  );
  const on = await readSetting(isolatedEnv(dir, {}));
  assert.equal(on.sealing, "on");
  assert.equal(on.enrollToolSecrets, false);
  assert.equal(on.server.baseUrl, "http://x:1933");
  assert.equal(on.server.headers.Authorization, "Bearer sk-test");
  assert.equal((await readSetting(isolatedEnv(dir, { OPENVIKING_ENROLL_TOOL_SECRETS: "1" }))).enrollToolSecrets, true);
  assert.deepEqual(await readSetting(isolatedEnv(dir, { OPENVIKING_SEAL_SECRETS: "0" })), { sealing: "off" });

  writeFileSync(
    join(dir, "ovcli.conf"),
    JSON.stringify({ url: "http://x:1933", plugin: { claude_code: { sealSecrets: true, enrollToolSecrets: true } } }),
  );
  assert.equal((await readSetting(isolatedEnv(dir, {}))).enrollToolSecrets, true);
});

// One stub /health, answering `version` (or failing with 503 when it is null).
async function healthServer(t, version) {
  const seen = [];
  const server = createServer((req, res) => {
    seen.push({ url: req.url, auth: req.headers.authorization });
    const body = version === null ? { status: "error" } : { status: "ok", healthy: true, version };
    res.writeHead(version === null ? 503 : 200, { "content-type": "application/json" });
    res.end(JSON.stringify(body));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  return { url: `http://127.0.0.1:${server.address().port}`, seen };
}

test("the setting reports whether the server supports sealing, from one /health", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "cc-seal-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  for (const [version, supported] of [["0.5.7.dev44", true], ["0.6.1", true], ["0.5.6", false], [null, false]]) {
    const health = await healthServer(t, version);
    const env = isolatedEnv(dir, { OPENVIKING_SEAL_SECRETS: "1", OPENVIKING_URL: health.url, OPENVIKING_API_KEY: "sk-h" });
    const on = await readSetting(env);
    assert.equal(on.sealing, "on");
    assert.deepEqual(on.support, { feature: "privacy_seal", supported, required: "0.5.7", actual: version }, String(version));
    assert.deepEqual(health.seen, [{ url: "/health", auth: "Bearer sk-h" }]);
  }
  const unreachable = await readSetting(isolatedEnv(dir, {
    OPENVIKING_SEAL_SECRETS: "1",
    OPENVIKING_URL: "http://127.0.0.1:9",
  }));
  assert.equal(unreachable.support.supported, false);
  assert.equal(unreachable.support.actual, null);
});
