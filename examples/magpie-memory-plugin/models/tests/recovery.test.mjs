import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { startAdapter, synchronize } from "../lib/runtime.mjs";
import { activate } from "../lib/activation.mjs";

for (const failure of ["transport", 503, 403]) {
  test(`background disconnect retains pending activation after initial GET failure: ${failure}`, async (t) => {
    const directory = await fs.mkdtemp(join(tmpdir(), "ov-restoration-recovery-"));
    const previous = { ...process.env };
    process.env.OPENVIKING_MAGPIE_MODELS_DIR = directory;
    process.env.OPENVIKING_CLI_CONFIG_FILE = join(directory, "ovcli.conf");
    process.env.PATH = ""; // Make optional CLI discovery deterministic.
    let stop, releaseJournal;
    let document = { vlm: { model: "original", provider: "openai", api_key: "original-key" } };
    const original = structuredClone(document);
    let revision = "r0", pending = false, instance = "initial", restarts = 0;
    let restoreSaved = false, failing = true, configurationReads = 0;
    const server = createServer(async (request, response) => {
      const send = (value, code = 200) => {
        response.writeHead(code, { "Content-Type": "application/json" });
        response.end(JSON.stringify(value));
      };
      if (request.url === "/v1/models") return send({ data: [{ id: "provider/selected" }] });
      if (request.url === "/bot/v1/health") return send({}, 503);
      if (request.url === "/api/v1/admin/restart") {
        restarts++;
        pending = false;
        instance = "restored-instance";
        return send({ status: "ok", result: {} });
      }
      if (request.method === "PATCH") {
        let body = "";
        for await (const chunk of request) body += chunk;
        if (!request.url.includes("dry_run")) {
          const patch = JSON.parse(body);
          assert.equal(patch.revision, revision);
          document = JSON.parse(patch.content);
          revision = revision === "r0" ? "r1" : "r2";
          pending = true;
          restoreSaved = document.vlm.model === "original";
        }
      } else {
        configurationReads++;
        if (restoreSaved && failing) {
          if (failure === "transport") return request.socket.destroy();
          return send({}, failure);
        }
      }
      send({ status: "ok", result: { content: JSON.stringify(document), revision,
        file_path: "/server/ov.conf", writable: true, restart_required: pending,
        restart: { supported: true, instance_id: instance, restarting: false } } });
    });
    t.after(async () => {
      releaseJournal?.();
      await stop?.();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      for (const key of ["OPENVIKING_MAGPIE_MODELS_DIR", "OPENVIKING_CLI_CONFIG_FILE", "PATH"]) {
        if (previous[key] === undefined) delete process.env[key]; else process.env[key] = previous[key];
      }
      await fs.rm(directory, { recursive: true, force: true });
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const serverUrl = `http://127.0.0.1:${server.address().port}`;
    await fs.writeFile(process.env.OPENVIKING_CLI_CONFIG_FILE, JSON.stringify({ url: serverUrl }));
    const config = { directory, selectionFile: join(directory, "selection.json"),
      stateFile: join(directory, "state.json"), statusFile: join(directory, "status.json"),
      serverUrl, manageVlm: true, botMode: "preserve", queryMode: "preserve", timeoutMs: 1000 };
    await fs.writeFile(config.selectionFile, JSON.stringify({ model: "provider/selected",
      gateway: { baseURL: `${serverUrl}/v1`, key: "gateway-key" } }));
    await synchronize(config);
    pending = false; // The original binding has been activated before disconnect.
    await fs.writeFile(config.selectionFile, "{}");
    const status = async () => JSON.parse(await fs.readFile(config.statusFile, "utf8"));
    const waitFor = async (predicate) => {
      const deadline = Date.now() + 5000;
      while (Date.now() < deadline) {
        const current = await status();
        if (predicate(current)) return current;
        await delay(20);
      }
      assert.fail(`Timed out; status=${JSON.stringify(await status())}`);
    };
    stop = startAdapter({}, { serverUrl, pollMs: 500 });
    const code = failure === "transport" ? "server_unreachable" : failure === 403 ? "root_permission_required" : "server_http_503";
    const failed = await waitFor((value) => value.code === code);
    assert.equal(failed.status, failure === 403 ? "apply_failed" : "restored");
    assert.equal(failed.revision, "r2");
    assert.equal(failed.savedRevision, "r2");
    assert.equal(failed.restartRequired, true);
    assert.deepEqual(document, original);
    assert.equal(pending, true);
    assert.equal(restarts, 0);
    await assert.rejects(fs.access(config.stateFile), { code: "ENOENT" });

    if (failure === 403) {
      const reads = configurationReads;
      await delay(1100);
      assert.equal(configurationReads, reads, "definitive failure must not automatically retry activation");
      assert.equal((await status()).status, "apply_failed");
      await stop();
      failing = false;
      assert.equal((await activate(config, { retry: true })).status, "applied", "explicit recovery remains actionable");
    } else {
      // Hold the final journal write after status.json announces "applied".
      // Shutdown must wait for this write and the enclosing lock cleanup.
      let journalEntered;
      const entered = new Promise((resolve) => { journalEntered = resolve; });
      const release = new Promise((resolve) => { releaseJournal = resolve; });
      const rename = fs.rename.bind(fs);
      t.mock.method(fs, "rename", async (from, to) => {
        if (to === join(directory, "activation.json")) {
          const journal = JSON.parse(await fs.readFile(from, "utf8"));
          if (journal.status === "complete") { journalEntered(); await release; }
        }
        return rename(from, to);
      });
      failing = false;
      await waitFor((value) => value.status === "applied");
      await entered;
      let drained = false;
      const stopping = Promise.resolve(stop()).then(() => { drained = true; });
      await delay(0);
      assert.equal(drained, false, "shutdown must wait for the active journal write");
      await fs.access(join(directory, "adapter.lock"));
      releaseJournal();
      await stopping;
      assert.equal(JSON.parse(await fs.readFile(join(directory, "activation.json"), "utf8")).status, "complete");
    }
    await assert.rejects(fs.access(join(directory, "adapter.lock")), { code: "ENOENT" });
    assert.equal(restarts, 1);
    assert.equal(pending, false);
    assert.equal((await status()).restartRequired, false);
  });
}
