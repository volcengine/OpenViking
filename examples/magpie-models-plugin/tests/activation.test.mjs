import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { activate } from "../lib/activation.mjs";
import { writeJSON, readJSON } from "../lib/files.mjs";
import { detectBot } from "../lib/api.mjs";
import { createAPI } from "../lib/api.mjs";
import { cliConnection } from "../lib/config.mjs";
import { serverCLIInstalled, doctor } from "../lib/doctor.mjs";

async function fixture(t) {
  const directory = await fs.mkdtemp(join(tmpdir(), "ov-models-activation-"));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const config = { directory, statusFile: join(directory, "status.json"), serverUrl: "http://127.0.0.1:1933", apiKey: "root-test", timeoutMs: 100, restartTimeoutMs: 1000 };
  await writeJSON(config.statusFile, { status: "saved", revision: "rev-1", restartRequired: true, ovRuntime: "restart_required", botRuntime: "restart_or_verify_required" });
  let clock = 0, requested = false;
  const backend = { posts: 0, supported: true, complete: true, alreadyRestarted: false, rolledBack: false, loseReply: false, botStatus: 200, revision: "rev-1", reads: 0, filePath: "/fake/ov.conf",
    async fetcher(url, init) {
      assert.equal(init.headers["X-API-Key"], "root-test");
      if (url.endsWith("/bot/v1/health")) return Response.json({ status: "healthy" }, { status: backend.botStatus });
      if (init.method === "POST") {
        assert.ok(url.endsWith("/api/v1/admin/restart"));
        assert.deepEqual(JSON.parse(init.body), { revision: "rev-1" });
        backend.posts++; requested = true;
        if (backend.loseReply) throw new Error("lost accepted reply");
        return Response.json({ status: "ok", result: { restarting: true } }, { status: 202 });
      }
      backend.reads++;
      const complete = (requested || backend.alreadyRestarted) && backend.complete;
      return Response.json({ status: "ok", result: { revision: backend.revision, file_path: backend.filePath, restart_required: !complete,
        restart: { supported: backend.supported, instance_id: complete ? "new-instance" : "old-instance", restarting: false, rolled_back: requested && backend.rolledBack } } });
    },
  };
  return { config, backend, run: (options = {}) => activate(config, { fetcher: backend.fetcher, now: () => clock, wait: async (ms) => { clock += ms; }, ...options }) };
}

test("restart is POSTed once and both new OV instance and managed Bot health are verified", async (t) => {
  const f = await fixture(t);
  const applied = await f.run();
  assert.equal(applied.status, "applied");
  assert.equal(applied.instanceId, "new-instance");
  assert.equal(applied.botRuntime, "managed_restart_verified");
  assert.equal(applied.restartRequired, false);
  assert.equal(f.backend.posts, 1);
  await f.run();
  assert.equal(f.backend.posts, 1);
});
test("an unchanged process is not success; failed automatic attempts do not restart-loop", async (t) => {
  const f = await fixture(t); f.backend.complete = false;
  const failed = await f.run();
  assert.equal(failed.status, "apply_failed");
  assert.equal(failed.code, "server_restart_timeout");
  await f.run(); assert.equal(f.backend.posts, 1);
  f.backend.complete = true;
  assert.equal((await f.run({ retry: true })).status, "applied");
});
test("a lost accepted response is observed instead of blindly sending another restart", async (t) => {
  const f = await fixture(t); f.backend.loseReply = true;
  assert.equal((await f.run()).status, "applied");
  assert.equal(f.backend.posts, 1);
});
test("server rollback remains a visible failure", async (t) => {
  const f = await fixture(t); f.backend.rolledBack = true;
  const result = await f.run();
  assert.equal(result.code, "server_restart_rolled_back");
  assert.equal(result.restartRequired, true);
});
test("changed config and unsupported lifecycle block restart before POST", async (t) => {
  const f = await fixture(t); f.backend.revision = "external-edit";
  await assert.rejects(f.run(), /config_changed_before_apply/);
  f.backend.revision = "rev-1"; f.backend.supported = false;
  await assert.rejects(f.run(), /server_restart_unsupported/);
  assert.equal(f.backend.posts, 0);
});
test("Bot-only changes cannot restart OV in place of a standalone/disabled Bot", async (t) => {
  const f = await fixture(t); f.backend.botStatus = 503;
  await writeJSON(f.config.statusFile, { status: "saved", revision: "rev-1", ovRuntime: "not_managed", botRuntime: "restart_or_verify_required" });
  await assert.rejects(f.run(), /standalone_bot_restart_required/);
  assert.equal(f.backend.posts, 0);
});
test("OV can restart while Bot is disabled, without ever enabling Bot", async (t) => {
  const f = await fixture(t); f.backend.botStatus = 503;
  await writeJSON(f.config.statusFile, { status: "saved", revision: "rev-1", ovRuntime: "restart_required", botRuntime: "not_managed" });
  const result = await f.run();
  assert.equal(result.botRuntime, "not_managed");
  assert.equal(result.restartRequired, false);
  assert.equal(f.backend.posts, 1);
});
test("Bot detection distinguishes disabled, unsupported and unhealthy managed service", async () => {
  for (const [code, enabled, state] of [[503, false, "disabled"], [404, false, "unsupported"], [502, true, "unhealthy"], [403, false, "unknown"]]) {
    const value = await detectBot({ serverUrl: "https://ov.example", timeoutMs: 100 }, async () => new Response("{}", { status: code }));
    assert.deepEqual(value, { enabled, healthy: false, state });
  }
});
test("CLI config discovery uses captured JSON URL, never masked credentials", async (t) => {
  const directory = await fs.mkdtemp(join(tmpdir(), "ov-cli-config-"));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const executable = join(directory, "ov");
  await fs.writeFile(executable, '#!/bin/sh\nprintf \'%s\\n\' \'{"ok":true,"result":{"url":"https://cli.example","root_api_key":"***"}}\'\n', { mode: 0o700 });
  const result = await cliConnection(join(directory, "ovcli.conf"), process.env, executable);
  assert.deepEqual(result, { url: "https://cli.example" });
  assert.equal(await readJSON(join(directory, "nothing.json")), null);
});
test("a recovered host verifies a completed pending restart without another POST", async (t) => {
  const f = await fixture(t); f.backend.alreadyRestarted = true;
  await writeJSON(join(f.config.directory, "activation.json"), { serverUrl: f.config.serverUrl, revision: "rev-1", instanceId: "old-instance", status: "requested" });
  const result = await f.run();
  assert.equal(result.status, "applied");
  assert.equal(result.botRuntime, "managed_restart_verified");
  assert.equal(f.backend.posts, 0);
});
test("configuration API rejects missing or insufficient Root credentials explicitly", async () => {
  for (const [code, message] of [[401, "root_authentication_required"], [403, "root_permission_required"]]) {
    const api = createAPI({ serverUrl: "https://ov.example", timeoutMs: 100 }, async () => new Response("private error body", { status: code }));
    await assert.rejects(api("/api/v1/admin/configuration?source=file"), new RegExp(message));
  }
});
test("local CLI installation and remote runtime capability are independent", async (t) => {
  const directory = await fs.mkdtemp(join(tmpdir(), "ov-server-detection-"));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const executable = join(directory, "openviking-server");
  await fs.writeFile(executable, "#!/bin/sh\nexit 99\n", { mode: 0o700 });
  assert.equal(await serverCLIInstalled({ PATH: directory }), true);
  const config = { serverUrl: "https://ov.example", timeoutMs: 100 };
  const result = await doctor(config, { env: { PATH: "" }, fetcher: async (url) => url.endsWith("/health")
    ? new Response("{}", { status: 503 }) : Response.json({ status: "ok", result: { restart: { supported: true } } }) });
  assert.equal(result.localServerCLIInstalled, false);
  assert.equal(result.serverRestartSupported, true);
  assert.equal(result.bot, "disabled");
});


test("automatic apply rechecks revision ownership and target inside the activation lock", async (t) => {
  const f = await fixture(t);
  await assert.rejects(f.run({ automatic: true }), /configuration_not_saved_by_plugin/);
  await writeJSON(f.config.statusFile, { status: "saved", revision: "rev-1", savedRevision: "rev-1", ovRuntime: "restart_required", botRuntime: "not_managed" });
  const savedFile = join(f.config.directory, "saved.json");
  const own = { serverUrl: f.config.serverUrl, filePath: f.backend.filePath, revision: "rev-1" };
  for (const foreign of [{ ...own, serverUrl: "https://another.example" }, { ...own, revision: "external-edit" }, { ...own, filePath: "/other/ov.conf" }]) {
    await writeJSON(savedFile, foreign);
    await assert.rejects(f.run({ automatic: true }), /configuration_not_saved_by_plugin/);
  }
  assert.equal(f.backend.posts, 0);
  await writeJSON(savedFile, own);
  assert.equal((await f.run({ automatic: true })).status, "applied");
  assert.equal(f.backend.posts, 1);
});
