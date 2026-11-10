// Opt-in actual-host contract. All Magpie state is isolated in a temporary
// child-process home. Fake OV and vendor endpoints; no paid inference.
import fs from "node:fs/promises";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { tmpdir, homedir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import assert from "node:assert/strict";

if (!process.env.MAGPIE_BIN) throw new Error("Set MAGPIE_BIN to the installed Magpie executable.");
const root = await fs.mkdtemp(join(tmpdir(), "ov-models-magpie-"));
const childHome = join(root, "home");
assert.notEqual(resolve(childHome), resolve(homedir()));
// Build from a small allowlist instead of inheriting credentials, config paths
// or implicit runtime overrides from the developer's actual agent processes.
// An explicit MAGPIE_BUN can reuse the installed runtime without downloading.
const env = {
  PATH: process.env.PATH, TMPDIR: process.env.TMPDIR || tmpdir(),
  HOME: childHome, USERPROFILE: childHome, XDG_CONFIG_HOME: join(childHome, ".config"),
  XDG_CACHE_HOME: join(childHome, ".cache"), XDG_DATA_HOME: join(childHome, ".local/share"),
  APPDATA: join(childHome, "AppData/Roaming"), LOCALAPPDATA: join(childHome, "AppData/Local"),
  MAGPIE_NO_ACCOUNTS: "1", DO_NOT_TRACK: "1",
  ...(process.env.MAGPIE_BUN ? { MAGPIE_BUN: process.env.MAGPIE_BUN } : {}),
};
const initial = { vlm: { provider: "openai", model: "original", api_key: "fake-original" },
  embedding: { dense: { provider: "openai", model: "fixed-embedding", dimension: 1536 } },
  bot: { agents: { model: "old-bot", provider: "openai", apiKey: "fake-bot" } } };
let document = structuredClone(initial), revision = 0, inferenceCalls = 0, service;
let logs = "";
const backend = createServer(async (req, res) => {
  res.setHeader("Content-Type", "application/json");
  let raw = ""; for await (const chunk of req) raw += chunk;
  const body = raw ? JSON.parse(raw) : null;
  if (req.url === "/bot/v1/health") return res.end(JSON.stringify({ status: "healthy" }));
  if (req.url === "/v1/models") return res.end(JSON.stringify({ data: ["one", "two"].map((id) => ({ id, context_window: 64000 })) }));
  if (!req.url.startsWith("/api/v1/admin/configuration")) {
    inferenceCalls++; res.statusCode = 500; return res.end("{}");
  }
  if (req.method === "GET") return res.end(JSON.stringify({ status: "ok", result: {
    content: JSON.stringify(document), revision: String(revision), writable: true, file_path: "/fake/ov.conf", overrides: { cluster: [] }, restart_required: revision > 0,
  } }));
  if (!req.url.includes("dry_run=true")) {
    if (body.revision !== String(revision)) { res.statusCode = 400; return res.end("{}"); }
    document = JSON.parse(body.content); revision++;
  }
  res.end(JSON.stringify({ status: "ok", result: { revision: String(revision), restart_required: true } }));
});
await new Promise((done) => backend.listen(0, "127.0.0.1", done));
const backendURL = `http://127.0.0.1:${backend.address().port}`;
const reserve = createServer();
await new Promise((done) => reserve.listen(0, "127.0.0.1", done));
const port = reserve.address().port;
await new Promise((done) => reserve.close(done));
env.MAGPIE_ADDR = `127.0.0.1:${port}`;
const bin = process.env.MAGPIE_BIN;
async function command(...args) {
  return new Promise((done, reject) => {
    const child = spawn(bin, args, { env, stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    child.stdout.on("data", (chunk) => { output += chunk; });
    child.stderr.on("data", (chunk) => { output += chunk; });
    const timer = setTimeout(() => child.kill("SIGKILL"), 30000);
    child.on("error", reject);
    child.on("close", (code) => { clearTimeout(timer); code === 0 ? done(output) : reject(new Error(`Magpie ${args[0]} failed: ${output.slice(-2500)}`)); });
  });
}
async function until(predicate, label) {
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) { if (await predicate()) return; await delay(200); }
  throw new Error(`Timed out: ${label}\n${logs.slice(-3000)}`);
}
try {
  await fs.mkdir(childHome, { recursive: true });
  const plugin = join(root, "plugin");
  await fs.mkdir(plugin);
  const source = dirname(dirname(fileURLToPath(import.meta.url)));
  for (const file of ["package.json", "index.mjs", "openviking.agent.js", "lib", "page"]) await fs.cp(join(source, file), join(plugin, file), { recursive: true });
  const adapterDirectory = join(env.XDG_CONFIG_HOME, "magpie-openviking-models");
  await fs.mkdir(adapterDirectory, { recursive: true, mode: 0o700 });
  await fs.writeFile(join(adapterDirectory, "adapter.json"), JSON.stringify({ serverUrl: backendURL, pollMs: 500, botMode: "follow" }));
  console.log((await command("--version")).trim());
  await command("plugin", "add", plugin);
  await command("provider", "add", "smoke", `url=${backendURL}/v1`, "key=fake-key", "models=one,two", "context=64000");
  const settingsFile = join(env.XDG_CONFIG_HOME, "magpie", "settings.json");
  await fs.mkdir(dirname(settingsFile), { recursive: true });
  let settings = {};
  try { settings = JSON.parse(await fs.readFile(settingsFile, "utf8")); } catch (error) { if (error.code !== "ENOENT") throw error; }
  await fs.writeFile(settingsFile, JSON.stringify({ ...settings, port }));
  assert.match(await command("agents"), /OpenViking/);
  service = spawn(bin, ["serve"], { env, stdio: ["ignore", "pipe", "pipe"] });
  service.stdout.on("data", (chunk) => { logs += chunk; });
  service.stderr.on("data", (chunk) => { logs += chunk; });
  await command("openviking-models", "smoke/one");
  await until(() => document.vlm.model === "smoke/one", "first synchronization");
  assert.equal(document.bot.agents.model, undefined);
  assert.equal(document.bot.agents.contextWindowTokens, 64000);
  assert.deepEqual(document.embedding, initial.embedding);
  await command("openviking-models", "smoke/two");
  await until(() => document.vlm.model === "smoke/two", "model change");
  await command("openviking-models", "default");
  await until(() => document.vlm.model === "original", "disconnect restoration");
  assert.deepEqual(document, initial);
  assert.equal(inferenceCalls, 0);
  console.log("PASS: actual Magpie Agent registration, Bun bridge, catalog, Bot inheritance, model change and restoration; zero inference calls.");
} finally {
  if (service && service.exitCode === null) {
    await new Promise((done) => {
      service.once("close", done); service.kill("SIGTERM");
      const timer = setTimeout(() => service.kill("SIGKILL"), 2000); timer.unref();
    });
  }
  backend.closeAllConnections();
  await new Promise((done) => backend.close(done));
  await fs.rm(root, { recursive: true, force: true });
}
