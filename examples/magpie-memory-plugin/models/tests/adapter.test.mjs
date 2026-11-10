import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { AsyncLocalStorage } from "node:async_hooks";
import { activate } from "../lib/activation.mjs";
import { synchronize, status } from "../lib/runtime.mjs";
import { settings, normalizeURL } from "../lib/config.mjs";
import { readJSON, withLock } from "../lib/files.mjs";
import { desiredSettings, plan } from "../lib/plan.mjs";
import { agent, connect } from "../openviking.agent.js";
import { modelParameters, validateParameters } from "../lib/parameters.mjs";

const selection = { model: "vendor/chat", gateway: { baseURL: "http://127.0.0.1:3425/v1", key: "magpie-openviking-models" }, metadata: { context: 64000 } };
const original = {
  server: { root_api_key: "never-print-this" },
  vlm: { model: "old", provider: "volcengine", api_key: "old-secret", credentials: [{ model: "old-endpoint", api_key: "old-secret" }], providers: { volcengine: { api_key: "old-secret" } }, backup: { model: "backup" }, temperature: 0.2 },
  embedding: { dense: { model: "fixed", dimension: 2048 } },
  query_planner: { model: "planner" },
  bot: { agents: { model: "old-bot", provider: "openai", apiKey: "bot-secret", contextWindowTokens: 128000, maxToolIterations: 30 }, channels: [{ type: "feishu" }] },
};
async function fixture(t, document = original) {
  const directory = await fs.mkdtemp(join(tmpdir(), "ov-models-test-"));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  let current = structuredClone(document), revision = 1, restart = false;
  const requests = [], config = { directory, selectionFile: join(directory, "selection.json"), stateFile: join(directory, "state.json"), statusFile: join(directory, "status.json"), serverUrl: "http://127.0.0.1:1933", apiKey: "root-secret", manageVlm: true, botMode: "preserve", timeoutMs: 1000 };
  await fs.writeFile(config.selectionFile, JSON.stringify(selection));
  const backend = {
    overrides: [], failSave: false, lostResponse: false, failDryRun: false, output: undefined, botStatus: 200,
    get document() { return current; },
    edit(fn) { fn(current); revision++; restart = true; },
    markApplied() { restart = false; },
    async fetcher(url, init) {
      requests.push({ url, init });
      if (url.endsWith("/bot/v1/health")) return Response.json({ status: "healthy" }, { status: backend.botStatus });
      if (url.endsWith("/models")) return Response.json({ data: backend.catalog || [{ id: selection.model, context_window: 64000, max_output_tokens: backend.output }, { id: "vendor/bot", context_window: 32000, max_output_tokens: 4096 }] });
      assert.equal(init.headers["X-API-Key"], "root-secret");
      if (init.method === "GET") return Response.json({ status: "ok", result: { content: JSON.stringify(current), revision: String(revision), file_path: "/server/ov.conf", writable: true, restart_required: restart, overrides: { cluster: backend.overrides } } });
      const body = JSON.parse(init.body);
      if (url.endsWith("dry_run=true")) {
        if (backend.failDryRun) return new Response("secret validation details", { status: 400 });
        return Response.json({ status: "ok", result: { content: body.content } });
      }
      if (backend.failSave || body.revision !== String(revision)) return new Response("secret conflict details", { status: 400 });
      current = JSON.parse(body.content); revision++; restart = true;
      if (backend.lostResponse) { backend.lostResponse = false; throw new Error("network disconnected after save"); }
      return Response.json({ status: "ok", result: { revision: String(revision), restart_required: true } });
    },
  };
  return { config, backend, requests, run: (opts = {}) => synchronize(config, { fetcher: backend.fetcher, ...opts }) };
}

test("Agent writes a selection, with full Magpie model id and metadata", () => {
  assert.equal(agent.model, "model"); assert.equal(agent.format, "json");
  assert.equal(connect({ gateway: { v1: selection.gateway.baseURL, key: selection.gateway.key }, model: { context: 64000 } })["gateway.baseURL"], selection.gateway.baseURL);
});
test("replace the entire legacy service binding, preserve other roles, restore exact original values", async (t) => {
  const f = await fixture(t);
  assert.equal((await f.run()).status, "saved");
  assert.equal(f.backend.document.vlm.model, selection.model);
  assert.equal(f.backend.document.vlm.provider, "openai");
  for (const key of ["credentials", "providers", "backup"]) assert.equal(key in f.backend.document.vlm, false);
  assert.equal(f.backend.document.vlm.temperature, 0.2);
  assert.deepEqual(f.backend.document.bot, original.bot);
  assert.deepEqual(f.backend.document.embedding, original.embedding);
  assert.deepEqual(f.backend.document.query_planner, original.query_planner);
  const saves = f.requests.filter((r) => r.init.method === "PATCH" && !r.url.includes("dry_run"));
  await f.run();
  assert.equal(f.requests.filter((r) => r.init.method === "PATCH" && !r.url.includes("dry_run")).length, saves.length);
  assert.equal((await f.run({ restore: true })).status, "restored");
  assert.deepEqual(f.backend.document, original);
  assert.equal((await status(f.config)).recoveryPending, false);
  assert.equal((await f.run()).status, "paused");
  assert.equal((await f.run({ resume: true })).status, "saved");
});
test("Bot follows root by removing explicit model and credential chain; preserves channels", async (t) => {
  const f = await fixture(t);
  f.config.botMode = "follow";
  f.backend.edit((doc) => { doc.bot.agents.credentials = [{ model: "explicit-chain" }]; });
  f.backend.markApplied();
  const before = structuredClone(f.backend.document);
  await f.run();
  assert.equal("model" in f.backend.document.bot.agents, false);
  assert.equal("credentials" in f.backend.document.bot.agents, false);
  assert.equal(f.backend.document.bot.agents.contextWindowTokens, 64000);
  assert.deepEqual(f.backend.document.bot.channels, original.bot.channels);
  await f.run({ restore: true });
  assert.deepEqual(f.backend.document, before);
});
test("Bot-only independent model uses its own catalog budget and leaves root unchanged", async (t) => {
  const f = await fixture(t);
  Object.assign(f.config, { manageVlm: false, botMode: "independent", botModel: "vendor/bot" });
  await f.run();
  assert.deepEqual(f.backend.document.vlm, original.vlm);
  assert.equal(f.backend.document.bot.agents.model, "vendor/bot");
  assert.equal(f.backend.document.bot.agents.provider, "openai");
  assert.equal(f.backend.document.bot.agents.contextWindowTokens, 32000);
});
test("manual edits to managed fields block apply and restore without leaking values", async (t) => {
  const f = await fixture(t); await f.run();
  f.backend.edit((doc) => { doc.vlm.api_key = "manual-secret"; });
  f.backend.markApplied();
  const expected = structuredClone(f.backend.document);
  const result = await f.run({ restore: true });
  assert.equal(result.status, "conflict");
  assert.deepEqual(result.conflicts, ["vlm.api_key"]);
  assert.deepEqual(f.backend.document, expected);
  assert.doesNotMatch(JSON.stringify(await status(f.config)), /manual-secret|old-secret|root-secret/);
  assert.equal((await status(f.config)).recoveryPending, true);
});
test("unrelated user edits survive restoration", async (t) => {
  const f = await fixture(t); await f.run();
  f.backend.edit((doc) => { doc.vlm.timeout = 900; doc.bot.channels.push({ type: "telegram" }); });
  assert.equal((await f.run({ restore: true })).status, "external_restart_required");
  assert.ok(await readJSON(f.config.stateFile));
  f.backend.markApplied();
  await f.run({ restore: true });
  assert.deepEqual(f.backend.document, { ...original, vlm: { ...original.vlm, timeout: 900 }, bot: { ...original.bot, channels: [...original.bot.channels, { type: "telegram" }] } });
});
test("lost save response recovers from write-ahead journal; original values still restore", async (t) => {
  const f = await fixture(t); f.backend.lostResponse = true;
  await assert.rejects(f.run(), /server_unreachable/);
  assert.equal((await status(f.config)).recoveryPending, true);
  await f.run(); await f.run({ restore: true });
  assert.deepEqual(f.backend.document, original);
});
test("failed save can be retried, but dry-run rejection never writes a journal or config", async (t) => {
  const f = await fixture(t); f.backend.failDryRun = true;
  await assert.rejects(f.run(), /server_http_400/);
  assert.equal(await readJSON(f.config.stateFile), null);
  assert.deepEqual(f.backend.document, original);
  f.backend.failDryRun = false; f.backend.failSave = true;
  await assert.rejects(f.run(), /server_http_400/);
  f.backend.failSave = false; await f.run(); await f.run({ restore: true });
  assert.deepEqual(f.backend.document, original);
});
test("disconnect restores missing sections, model reselection preserves the first original", async (t) => {
  const f = await fixture(t, { embedding: original.embedding });
  f.config.botMode = "independent"; await f.run();
  f.config.botMode = "preserve"; await f.run();
  assert.equal(f.backend.document.bot, undefined);
  await fs.rm(f.config.selectionFile); await f.run();
  assert.deepEqual(f.backend.document, { embedding: original.embedding });
});
test("Magpie disconnect leaves empty nested containers; restoration still completes", async (t) => {
  const f = await fixture(t); await f.run();
  await fs.writeFile(f.config.selectionFile, JSON.stringify({ gateway: {}, metadata: {} }));
  assert.equal((await f.run()).status, "restored");
  assert.deepEqual(f.backend.document, original);
});
test("a lost restore response is retried by the host while its selection is paused", async (t) => {
  const f = await fixture(t); await f.run(); f.backend.lostResponse = true;
  await assert.rejects(f.run({ restore: true }), /server_unreachable/);
  assert.equal((await f.run()).status, "restored");
  assert.deepEqual(f.backend.document, original);
  assert.equal((await f.run()).status, "paused");
});
test("runtime overrides, changed backend and incomplete selections cannot silently redirect writes", async (t) => {
  const f = await fixture(t); f.backend.overrides = ["vlm"];
  await assert.rejects(f.run(), /runtime_vlm_override/);
  f.backend.overrides = []; await f.run();
  f.config.serverUrl = "https://another.example";
  await assert.rejects(f.run(), /recovery_target_changed/);
  f.config.serverUrl = "http://127.0.0.1:1933";
  await fs.writeFile(f.config.selectionFile, '{"model":"incomplete"}');
  await assert.rejects(f.run(), /incomplete_selection/);
});
test("private recovery journal and concurrent host/CLI exclusion", async (t) => {
  const f = await fixture(t); await f.run();
  assert.equal((await fs.stat(f.config.stateFile)).mode & 0o777, 0o600);
  assert.equal((await fs.stat(f.config.directory)).mode & 0o777, 0o700);
  await withLock(f.config.directory, () => assert.rejects(f.run(), /adapter_locked/));
});
test("settings reuse Root credential, reject insecure remotes and keep selection in a private directory", async (t) => {
  const f = await fixture(t);
  const cli = join(f.config.directory, "ovcli.conf");
  await fs.writeFile(cli, JSON.stringify({ url: "https://ov.example", api_key: "user-secret", root_api_key: "root-secret" }));
  const cfg = await settings({ ovcliConfig: cli }, { OPENVIKING_MAGPIE_MODELS_DIR: f.config.directory });
  assert.equal(cfg.apiKey, "root-secret");
  assert.equal(cfg.botMode, "preserve");
  for (const url of ["http://remote.example", "https://user:pass@ov.example", "https://ov.example?key=secret"]) assert.throws(() => normalizeURL(url));
  await assert.rejects(settings({ ovcliConfig: cli, manageVlm: false, botMode: "follow" }, { OPENVIKING_MAGPIE_MODELS_DIR: f.config.directory }), /invalid_model_roles/);
});
test("unknown context metadata never overwrites an independently selected model budget", () => {
  const result = desiredSettings({ ...selection, botMetadata: { context: null } }, { manageVlm: true, botMode: "independent", botModel: "other" });
  assert.equal(result["bot.agents.contextWindowTokens"], undefined);
  assert.throws(() => plan({}, {}, { "server.root_api_key": { exists: true, value: "no" } }), /invalid_journal/);
});
test("max_tokens follows output limits on successive models and restores the original request", async (t) => {
  const f = await fixture(t);
  f.backend.edit((doc) => { doc.vlm.max_tokens = 16000; });
  f.backend.markApplied();
  f.backend.output = 8192;
  const saved = await f.run();
  assert.equal(f.backend.document.vlm.max_tokens, 8192);
  assert.deepEqual(saved.adjustments[0], { field: "vlm.max_tokens", requested: 16000, value: 8192, reason: "model_output_limit" });
  f.backend.output = 32000; await f.run();
  assert.equal(f.backend.document.vlm.max_tokens, 16000);
  await f.run({ restore: true });
  assert.equal(f.backend.document.vlm.max_tokens, 16000);
});
test("Bot-specific output override is capped independently; provider defaults stay unset", async (t) => {
  const f = await fixture(t);
  Object.assign(f.config, { botMode: "independent", botModel: "vendor/bot", vlmParameters: { max_tokens: null, temperature: 0.4 }, botParameters: { max_tokens: 12000, thinking: false, timeout: 90 } });
  await f.run();
  assert.equal(f.backend.document.vlm.max_tokens, null);
  assert.equal(f.backend.document.vlm.temperature, 0.4);
  assert.equal(f.backend.document.bot.agents.maxTokens, 4096);
  assert.equal(f.backend.document.bot.agents.thinking, false);
  assert.equal(f.backend.document.bot.agents.timeout, 90);
  await f.run({ restore: true });
  assert.deepEqual(f.backend.document, original);
});
test("unknown output capacity is reported instead of inventing a budget", async (t) => {
  const f = await fixture(t);
  f.backend.edit((doc) => { doc.vlm.max_tokens = 16000; });
  f.backend.markApplied();
  const saved = await f.run();
  assert.equal(f.backend.document.vlm.max_tokens, 16000);
  assert.ok(saved.warnings.includes("vlm:output_limit_unknown"));
});
test("unsupported temperature resets inherited values but rejects an explicit request", () => {
  const selected = { ...selection, vlmMetadata: { output: 8192, temperature: false } };
  const desired = {};
  const result = modelParameters(original, {}, desired, selected, { manageVlm: true, botMode: "preserve" });
  assert.equal(desired["vlm.temperature"].value, null);
  assert.equal(result.adjustments[0].reason, "model_temperature_unsupported");
  assert.throws(() => modelParameters(original, {}, {}, selected, { manageVlm: true, botMode: "preserve", vlmParameters: { temperature: 0.5 } }), /model_temperature_unsupported/);
  for (const value of [{ max_tokens: 0 }, { timeout: -1 }, { thinking: "true" }, { temperature: 3 }, { api_key: "bad" }]) assert.throws(() => validateParameters(value), /invalid_model_parameters/);
});
test("restart diagnostics distinguish OV and Bot-only changes and survive disconnect polling", async (t) => {
  const f = await fixture(t);
  Object.assign(f.config, { manageVlm: false, botMode: "independent" });
  const saved = await f.run();
  assert.equal(saved.ovRuntime, "not_managed");
  assert.equal(saved.botRuntime, "restart_or_verify_required");
  await fs.rm(f.config.selectionFile);
  const restored = await f.run();
  assert.equal(restored.restartRequired, true);
  assert.deepEqual(await f.run(), restored);
});
test("primary credential output limit survives replacing its binding and restores exactly", async (t) => {
  const f = await fixture(t);
  f.backend.edit((doc) => { doc.vlm.max_tokens = 16000; doc.vlm.credentials[0].max_tokens = 4000; });
  f.backend.markApplied();
  const before = structuredClone(f.backend.document);
  f.backend.output = 2000; await f.run();
  assert.equal(f.backend.document.vlm.max_tokens, 2000);
  f.backend.output = 32000; await f.run();
  assert.equal(f.backend.document.vlm.max_tokens, 4000);
  await f.run({ restore: true });
  assert.deepEqual(f.backend.document, before);
});
test("following Bot's independent output limit cannot exceed its new inherited model", async (t) => {
  const f = await fixture(t);
  f.config.botMode = "follow";
  f.backend.edit((doc) => { doc.bot.agents.maxTokens = 16000; });
  f.backend.markApplied();
  f.backend.output = 4096;
  await f.run();
  assert.equal(f.backend.document.bot.agents.maxTokens, 4096);
  assert.equal(f.backend.document.bot.agents.model, undefined);
});
test("a disabled Bot is neither configured nor enabled while changing OV VLM", async (t) => {
  const f = await fixture(t, { vlm: original.vlm, embedding: original.embedding });
  f.backend.botStatus = 503;
  const saved = await f.run();
  assert.equal(f.backend.document.bot, undefined);
  assert.equal(saved.botDetection, "disabled");
  assert.equal(saved.botRuntime, "not_managed");
  f.config.botMode = "independent";
  await assert.rejects(f.run(), /bot_not_enabled_or_detected/);
  assert.equal(f.backend.document.bot, undefined);
});

test("VLM concurrency is revision-saved, restart-pending and restored without touching Bot", async (t) => {
  const doc = structuredClone(original); doc.vlm.max_concurrent = 100;
  const f = await fixture(t, doc); f.config.vlmParameters = { max_concurrent: 4 };
  const result = await f.run();
  assert.equal(f.backend.document.vlm.max_concurrent, 4);
  assert.equal(result.restartRequired, true);
  assert.deepEqual(f.backend.document.bot, original.bot);
  await f.run({ restore: true });
  assert.equal(f.backend.document.vlm.max_concurrent, 100);
});

test("concurrency keeps omission distinct and rejects unsupported Bot values", () => {
  assert.deepEqual(validateParameters({ max_concurrent: 4 }), { max_concurrent: 4 });
  for (const value of [0, -1, 1.5, null, "4", Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => validateParameters({ max_concurrent: value }), /invalid_model_parameters/);
  assert.throws(() => validateParameters({ max_concurrent: 4 }, "bot"), /invalid_model_parameters/);
  const desired = {};
  modelParameters(original, {}, desired, selection, { manageVlm: true });
  assert.equal(Object.hasOwn(desired, "vlm.max_concurrent"), false);
});


test("native Bot mode switches ignore retained model ids and an empty independent id follows Agent selection", async (t) => {
  const f = await fixture(t), cli = join(f.config.directory, "ovcli.conf");
  await fs.writeFile(cli, JSON.stringify({ url: f.config.serverUrl }));
  const env = { PATH: "", OPENVIKING_MAGPIE_MODELS_DIR: f.config.directory };
  for (const botMode of ["follow", "preserve"]) {
    const cfg = await settings({ ovcliConfig: cli, botMode, botModel: "vendor/bot" }, env);
    assert.equal(cfg.botModel, undefined);
    assert.equal(cfg.botMode, botMode);
    Object.assign(f.config, { botMode: cfg.botMode, botModel: cfg.botModel });
    await f.run();
  }
  const cfg = await settings({ ovcliConfig: cli, botMode: "independent", botModel: "  " }, env);
  assert.equal(cfg.botModel, undefined);
  Object.assign(f.config, { botMode: cfg.botMode, botModel: cfg.botModel });
  await f.run();
  assert.equal(f.backend.document.bot.agents.model, selection.model);
  await assert.rejects(settings({ ovcliConfig: cli, botMode: "independent", botModel: {} }, env), /invalid_bot_model/);
});

test("only plugin-saved revisions remain eligible for automatic restart", async (t) => {
  const f = await fixture(t);
  const own = await f.run();
  assert.equal(own.savedRevision, own.revision);
  const unchanged = await f.run();
  assert.equal(unchanged.savedRevision, own.revision);
  f.backend.markApplied();
  // An unrelated operator edit is preserved but must never be claimed as ours.
  f.backend.edit((doc) => { doc.server.port = 9123; });
  const savesBefore = f.requests.filter((r) => r.init.method === "PATCH").length;
  const external = await f.run();
  assert.equal(external.status, "external_restart_required");
  assert.equal(external.restartRequired, true);
  assert.equal(external.savedRevision, undefined);
  assert.equal(f.requests.filter((r) => r.init.method === "PATCH").length, savesBefore);
  assert.equal(f.backend.document.server.port, 9123);
});

test("a lost save response and disconnect restoration retain exact revision ownership", async (t) => {
  const f = await fixture(t); f.backend.lostResponse = true;
  await assert.rejects(f.run(), /server_unreachable/);
  const recovered = await f.run();
  assert.equal(recovered.savedRevision, recovered.revision);
  const restored = await f.run({ restore: true });
  assert.equal(restored.savedRevision, restored.revision);
  assert.notEqual(restored.savedRevision, recovered.savedRevision);
  // With no journal left, the disconnected poll retains the restore's ownership.
  await fs.rm(f.config.selectionFile);
  assert.equal((await f.run()).savedRevision, restored.savedRevision);
});

test("simultaneous stale-lock recovery never deletes a new owner's lock", async (t) => {
  const f = await fixture(t), file = join(f.config.directory, "adapter.lock");
  await fs.writeFile(file, JSON.stringify({ pid: 99999999 }));
  const scope = new AsyncLocalStorage();
  let releaseInspection, inspectionStarted, entered, secondDone;
  const inspecting = new Promise((r) => { inspectionStarted = r; });
  const release = new Promise((r) => { releaseInspection = r; });
  const firstEntered = new Promise((r) => { entered = r; });
  const finishedSecond = new Promise((r) => { secondDone = r; });
  const originalRead = fs.readFile, originalOpen = fs.open, originalRemove = fs.rm;
  fs.readFile = async (name, ...args) => {
    const data = await originalRead(name, ...args);
    if (name === file) {
      if (scope.getStore() === "A") { inspectionStarted(); await release; }
      else if (scope.getStore() === "B") releaseInspection();
    }
    return data;
  };
  fs.open = async (name, ...args) => {
    try { return await originalOpen(name, ...args); }
    catch (error) {
      if (name === `${file}.reclaim` && scope.getStore() === "B" && error.code === "EEXIST") releaseInspection();
      throw error;
    }
  };
  fs.rm = async (name, ...args) => {
    if (name === file && scope.getStore() === "B") await firstEntered;
    return originalRemove(name, ...args);
  };
  let active = 0, peak = 0;
  const worker = (name) => scope.run(name, () => withLock(f.config.directory, async () => {
    peak = Math.max(peak, ++active);
    if (name === "A") { entered(); await finishedSecond; }
    active--;
  }));
  try {
    const first = worker("A"); await inspecting;
    const second = worker("B").finally(secondDone);
    const outcomes = await Promise.allSettled([first, second]);
    assert.equal(peak, 1, "at most one owner may enter the critical section");
    assert.equal(outcomes[0].status, "fulfilled");
    assert.equal(outcomes[1].status, "rejected");
    assert.match(outcomes[1].reason.message, /adapter_locked/);
  } finally {
    releaseInspection(); entered(); secondDone();
    fs.readFile = originalRead; fs.open = originalOpen; fs.rm = originalRemove;
  }
  await withLock(f.config.directory, async () => {});
});

test("a new model cannot claim or activate an external pending configuration", async (t) => {
  const f = await fixture(t);
  await f.run(); f.backend.markApplied();
  const saved = await readJSON(join(f.config.directory, "saved.json"));
  f.backend.edit((doc) => { doc.server.port = 9123; });
  await fs.writeFile(f.config.selectionFile, JSON.stringify({ ...selection, model: "vendor/bot" }));
  const writes = f.requests.filter((r) => r.init.method === "PATCH").length;
  const blocked = await f.run();
  assert.equal(blocked.status, "external_restart_required");
  assert.equal(blocked.savedRevision, undefined);
  assert.equal(f.backend.document.vlm.model, selection.model);
  assert.equal(f.requests.filter((r) => r.init.method === "PATCH").length, writes);
  assert.deepEqual(await readJSON(join(f.config.directory, "saved.json")), saved);
  f.backend.markApplied();
  assert.equal((await f.run()).status, "saved");
  assert.equal(f.backend.document.vlm.model, "vendor/bot");
  assert.equal(f.backend.document.server.port, 9123);
});
test("initial external pending changes block first model binding", async (t) => {
  const f = await fixture(t); f.backend.edit((doc) => { doc.server.port = 8123; });
  assert.equal((await f.run()).status, "external_restart_required");
  assert.equal(f.requests.some((r) => r.init.method === "PATCH"), false);
  assert.equal(await readJSON(f.config.stateFile), null);
});
test("a newer selection may replace the plugin's own pending revision", async (t) => {
  const f = await fixture(t); const first = await f.run();
  await fs.writeFile(f.config.selectionFile, JSON.stringify({ ...selection, model: "vendor/bot" }));
  const changed = await f.run();
  assert.equal(changed.status, "saved"); assert.notEqual(changed.savedRevision, first.savedRevision);
  assert.equal(f.backend.document.vlm.model, "vendor/bot");
});

test("independent Query Planner preserves generation settings, validates its own budget and restores exactly", async (t) => {
  const document = { ...original, query_planner: { model: "old-query", provider: "volcengine", credentials: [{ model: "old-endpoint", api_key: "query-secret", max_tokens: 8000 }], max_tokens: 16000, temperature: 0.3, timeout: 77, thinking: false } };
  const f = await fixture(t, document);
  Object.assign(f.config, { queryMode: "independent", queryModel: "vendor/bot" });
  const saved = await f.run();
  assert.equal(f.backend.document.vlm.model, "vendor/chat");
  assert.deepEqual(f.backend.document.query_planner, { model: "vendor/bot", provider: "openai", api_key: selection.gateway.key, api_base: selection.gateway.baseURL, max_tokens: 4096, temperature: 0.3, timeout: 77, thinking: false });
  assert.ok(saved.adjustments.some((x) => x.field === "query_planner.max_tokens" && x.requested === 8000));
  f.config.queryModel = "vendor/chat"; f.backend.output = 32000;
  await f.run();
  assert.equal(f.backend.document.query_planner.max_tokens, 8000);
  f.config.queryParameters = { max_tokens: 2000, timeout: 100, thinking: true, temperature: 0.5 };
  assert.ok((await f.run()).warnings.includes("query_planner:verify_thinking_support"));
  assert.equal(f.backend.document.query_planner.max_tokens, 2000);
  assert.equal(f.backend.document.query_planner.timeout, 100);
  await f.run({ restore: true });
  assert.deepEqual(f.backend.document, document);
});
test("Query follow removes the optional section and switching modes retains its original snapshot", async (t) => {
  const f = await fixture(t);
  Object.assign(f.config, { queryMode: "follow", queryModel: "stale/ignored" });
  await f.run();
  assert.equal(Object.hasOwn(f.backend.document, "query_planner"), false);
  f.config.queryMode = "independent"; f.config.queryModel = "vendor/bot";
  await f.run();
  assert.equal(f.backend.document.query_planner.model, "vendor/bot");
  f.config.queryMode = "preserve";
  await f.run();
  assert.deepEqual(f.backend.document.query_planner, original.query_planner);
  assert.equal(Object.hasOwn((await readJSON(f.config.stateFile)).records, "query_planner"), false);
  await f.run({ restore: true });
  assert.deepEqual(f.backend.document, original);
});
test("Query restoration retains original null or absence and recovers ambiguous saves", async (t) => {
  for (const query of [null, undefined]) {
    const document = { vlm: original.vlm, ...(query === null ? { query_planner: null } : {}) };
    const f = await fixture(t, document);
    Object.assign(f.config, { queryMode: "independent", queryModel: "vendor/bot" });
    f.backend.lostResponse = true;
    await assert.rejects(f.run(), /server_unreachable/);
    await f.run();
    await f.run({ restore: true });
    assert.deepEqual(f.backend.document, document);
  }
});
test("unavailable or non-chat Query models are refused before configuration writes", async (t) => {
  const f = await fixture(t);
  Object.assign(f.config, { queryMode: "independent", queryModel: "missing/model" });
  await assert.rejects(f.run(), /model_not_available/);
  f.backend.catalog = [{ id: selection.model }, { id: "missing/model", native_endpoints: ["/v1/embeddings"] }];
  await assert.rejects(f.run(), /chat_model_required/);
  assert.equal(f.requests.some((r) => r.init.method === "PATCH"), false);
  assert.equal(await readJSON(f.config.stateFile), null);
});
test("Query runtime overrides block management while account overrides remain out of scope", async (t) => {
  const f = await fixture(t); f.backend.overrides = ["query_planner"];
  Object.assign(f.config, { queryMode: "follow" });
  await assert.rejects(f.run(), /runtime_query_override/);
  f.config.queryMode = "independent";
  await assert.rejects(f.run(), /runtime_query_override/);
  assert.equal(f.requests.some((r) => r.init.method === "PATCH"), false);
  f.config.queryMode = "preserve";
  assert.equal((await f.run()).status, "saved");
  assert.deepEqual(f.backend.document.query_planner, original.query_planner);
});
test("manual edits to the owned Query section block restoration without disclosing credentials", async (t) => {
  const f = await fixture(t);
  Object.assign(f.config, { queryMode: "independent", queryModel: "vendor/bot" });
  await f.run();
  f.backend.edit((doc) => { doc.query_planner.api_key = "manual-query-secret"; });
  f.backend.markApplied();
  const result = await f.run({ restore: true });
  assert.equal(result.status, "conflict");
  assert.deepEqual(result.conflicts, ["query_planner"]);
  assert.doesNotMatch(JSON.stringify(result), /manual-query-secret/);
  assert.equal(f.backend.document.query_planner.api_key, "manual-query-secret");
});
test("Query-only management restarts OV without touching VLM or enabling Bot", async (t) => {
  const f = await fixture(t, { vlm: original.vlm });
  Object.assign(f.config, { manageVlm: false, queryMode: "independent", queryModel: "vendor/bot" });
  f.backend.botStatus = 503;
  assert.equal((await f.run()).ovRuntime, "restart_required");
  assert.deepEqual(f.backend.document.vlm, original.vlm);
  assert.equal(f.backend.document.bot, undefined);
  let posts = 0, instance = 1;
  const fetcher = async (url, init) => {
    if (init.method === "POST") { posts++; instance++; f.backend.markApplied(); return Response.json({ status: "ok", result: { restarting: true } }); }
    const response = await f.backend.fetcher(url, init);
    if (url.includes("/admin/configuration")) {
      const body = await response.json();
      body.result.restart = { supported: true, instance_id: String(instance) };
      return Response.json(body);
    }
    return response;
  };
  const result = await activate(f.config, { fetcher, automatic: true });
  assert.equal(result.status, "applied");
  assert.equal(result.restartRequired, false);
  assert.equal(result.botRuntime, "not_managed");
  assert.equal(posts, 1);
});
test("Query option validation defaults to preservation and ignores inactive model IDs", async (t) => {
  const f = await fixture(t);
  const env = { PATH: "", OPENVIKING_MAGPIE_MODELS_DIR: f.config.directory, OPENVIKING_CLI_CONFIG_FILE: join(f.config.directory, "missing-client") };
  assert.equal((await settings({}, env)).queryMode, "preserve");
  assert.equal((await settings({ queryModel: 123 }, env)).queryModel, undefined);
  await assert.rejects(settings({ queryMode: "bad" }, env), /invalid_query_mode/);
  await assert.rejects(settings({ queryMode: "follow", manageVlm: false }, env), /invalid_model_roles/);
  await assert.rejects(settings({ queryMode: "independent", queryModel: 123 }, env), /invalid_query_model/);
  await assert.rejects(settings({ queryParameters: { max_tokens: -1 } }, env), /invalid_model_parameters/);
});
