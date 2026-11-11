import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { createAPI, validateSelection } from "../lib/api.mjs";
import { activate } from "../lib/activation.mjs";
import { writeJSON } from "../lib/files.mjs";

const config = { serverUrl: "https://ov.example", timeoutMs: 1000 };
const selection = { model: "vendor/chat", gateway: { baseURL: "https://gateway.example/v1", key: "private-key" } };
const call = (service, fetcher, options = config) => service === "server"
  ? createAPI(options, fetcher)("/configuration") : validateSelection(selection, options, fetcher);
const brokenBody = (error) => new Response(new ReadableStream({ start(controller) { controller.error(error); } }));

for (const service of ["server", "gateway"]) {
  test(`${service} distinguishes header/body timeout, transport failure and malformed JSON`, async () => {
    for (const phase of ["headers", "body"]) {
      for (const [error, suffix] of [[new DOMException("private details", "TimeoutError"), "timeout"], [new TypeError("private details"), "unreachable"]]) {
        const fetcher = async () => { if (phase === "headers") throw error; return brokenBody(error); };
        await assert.rejects(call(service, fetcher), (failure) => {
          assert.equal(failure.code, `${service}_${suffix}`);
          assert.doesNotMatch(failure.message, /private details|private-key/);
          return true;
        });
      }
    }
    await assert.rejects(call(service, async () => new Response("not JSON")), {
      code: service === "server" ? "invalid_server_response" : "invalid_catalog",
    });
    await assert.rejects(call(service, async () => new Response("private details", { status: 403 })), {
      code: service === "server" ? "root_permission_required" : "gateway_http_403",
    });
  });

  test(`${service} recognizes a body AbortError caused by its timeout signal`, async () => {
    const fetcher = async (_, { signal }) => new Response(new ReadableStream({
      start(controller) {
        const abort = () => controller.error(new DOMException("body aborted", "AbortError"));
        if (signal.aborted) abort(); else signal.addEventListener("abort", abort, { once: true });
      },
    }));
    // Keep the event loop alive because AbortSignal.timeout uses an unref'ed timer.
    await Promise.all([
      assert.rejects(call(service, fetcher, { ...config, timeoutMs: 5 }), { code: `${service}_timeout` }),
      delay(20),
    ]);
  });
}

for (const phase of ["headers", "body"]) {
  test(`activation observes a timed-out restart ${phase} and retries timed-out verification without another POST`, async (t) => {
    const directory = await fs.mkdtemp(join(tmpdir(), "ov-api-timeout-"));
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    const options = { ...config, directory, statusFile: join(directory, "status.json"), restartTimeoutMs: 2000 };
    await writeJSON(options.statusFile, { status: "saved", revision: "1", ovRuntime: "restart_required", botRuntime: "not_managed" });
    let posts = 0, polls = 0, clock = 0;
    const timeout = () => {
      const error = new DOMException("private timeout", "TimeoutError");
      if (phase === "headers") throw error;
      return brokenBody(error);
    };
    const fetcher = async (_, init) => {
      if (init.method === "POST") { posts++; return timeout(); }
      if (posts && polls++ === 0) return timeout();
      return Response.json({ status: "ok", result: {
        revision: "1", restart_required: !posts,
        restart: { supported: true, instance_id: posts ? "new" : "old", restarting: false },
      } });
    };
    const result = await activate(options, { fetcher, now: () => clock, wait: async (ms) => { clock += ms; } });
    assert.equal(result.status, "applied");
    assert.equal(result.instanceId, "new");
    assert.equal(result.restartRequired, false);
    assert.equal(posts, 1);
    assert.equal(polls, 2);
  });
}
