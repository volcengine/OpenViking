/**
 * Every host under agent-hook-plugin/hosts/ is tested from its own directory:
 * the contract its host.mjs has to meet, and the cases in its fixtures/.
 *
 * A case is one JSON file with a `what` and one of:
 *   decode  { transcript?, cursor?, notes?, payload?, cfg? }  -> `messages`, the session
 *           messages the flow sends for it, and optionally `cursor` once all are settled
 *   read    { event, payload, transcript? }                    -> `expect`, the fields named
 *   tool    { payload }                                        -> `expect`, what the host prints
 *   emit    { event, output, payload? }                        -> `expect`, what the host prints
 *   mcp     { meta }                                           -> `expect`, the session an MCP
 *           tool call's `_meta` names, as the OpenViking session id
 * `transcript` names a file beside the case. UPDATE_FIXTURES=1 rewrites what a
 * case expects from what the host does now; read the diff before committing it.
 * OV_TEST_HOST=<directory> runs one host's cases only.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { loadHost } from "../agent-hook-plugin/hosts/index.mjs";
import { turnMessages } from "../agent-hook-plugin/lib/flow/capture.mjs";
import { toolCall } from "../agent-hook-plugin/lib/flow/index.mjs";
import { historySessionFor } from "../agent-hook-plugin/servers/mcp-proxy.mjs";
import { buildConfigForTest } from "./testing/support.mjs";

const HOSTS_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "agent-hook-plugin", "hosts");
const STAGES = ["start", "prompt", "turnEnd", "compactSoon", "end", "compacted", "toolCall", "toolResult"];
const UPDATE = process.env.UPDATE_FIXTURES === "1";
// Ids served by another host's directory.
const VARIANTS = { trae: ["trae-cn"] };

const directories = readdirSync(HOSTS_DIR, { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && existsSync(join(HOSTS_DIR, entry.name, "host.mjs")))
  .map((entry) => entry.name)
  .filter((name) => !process.env.OV_TEST_HOST || name === process.env.OV_TEST_HOST);

/** Every object in a hook configuration that names a command. */
function hookEntries(value, out = []) {
  if (Array.isArray(value)) for (const item of value) hookEntries(item, out);
  else if (value && typeof value === "object") {
    if (typeof value.command === "string") out.push(value);
    for (const item of Object.values(value)) hookEntries(item, out);
  }
  return out;
}

function outcome(host, dir, fixture) {
  const text = (spec) => (spec.transcript ? readFileSync(join(dir, spec.transcript), "utf8") : "");
  if (fixture.decode) {
    const spec = fixture.decode;
    const cfg = { ...buildConfigForTest(host.id), ...spec.cfg };
    const decoded = host.decode({
      text: text(spec),
      cursor: spec.cursor ?? {},
      notes: spec.notes ?? [],
      payload: spec.payload ?? {},
      cfg,
    });
    const result = { messages: turnMessages(decoded, cfg).filter(Boolean) };
    if ("cursor" in fixture) result.cursor = decoded.cursorAfter(decoded.turns.length);
    return result;
  }
  if (fixture.read) {
    const spec = fixture.read;
    const read = host.read(spec.event, spec.payload ?? {}, { text: () => (spec.transcript ? text(spec) : null) }) || {};
    return { expect: Object.fromEntries(Object.keys(fixture.expect ?? read).map((key) => [key, read[key]])) };
  }
  if (fixture.tool) {
    const { payload } = fixture.tool;
    return { expect: host.emit(fixture.tool.event ?? "", toolCall(host, payload), payload) ?? null };
  }
  if (fixture.mcp) return { expect: historySessionFor(host)?.({ _meta: fixture.mcp.meta }) ?? null };
  const spec = fixture.emit;
  return { expect: host.emit(spec.event, spec.output ?? {}, spec.payload ?? {}) ?? null };
}

for (const directory of directories) {
  const dir = join(HOSTS_DIR, directory);
  const ids = [directory, ...(VARIANTS[directory] || [])];

  test(`${directory}: host.mjs meets the contract`, async () => {
    for (const id of ids) {
      const host = await loadHost(id);
      assert.equal(host.id, id);
      assert.match(host.sessionPrefix, /^[a-z]+-$/);
      for (const [event, stage] of Object.entries(host.events)) {
        assert.ok(STAGES.includes(stage), `${event} maps to unknown stage ${stage}`);
      }
      for (const name of ["read", "decode", "emit"]) assert.equal(typeof host[name], "function", name);
      for (const event of Object.keys(host.budgets || {})) assert.ok(host.events[event], `budget for unknown event ${event}`);
    }
    // Every event a hook entry passes is one the host knows, with a request
    // budget that leaves the hook's timeout a few seconds for node start-up,
    // the session lock and the state write.
    const host = await loadHost(directory);
    const files = readdirSync(dir).filter((name) => name.endsWith(".json")).map((name) => join(dir, name));
    // Claude Code reads its hooks from the plugin root.
    if (directory === "claude-code") files.push(join(HOSTS_DIR, "..", "hooks", "hooks.json"));
    for (const path of files) {
      const file = basename(path);
      for (const entry of hookEntries(JSON.parse(readFileSync(path, "utf8")))) {
        const [, event, id] = /hook\.mjs\S*\s+([A-Za-z-]+)\s+([\w-]+)/.exec(entry.command) || [];
        if (!event) continue;
        // A template names the client the installer fills in.
        assert.ok(ids.includes(id) || /^__\w+__$/.test(id), `${file} runs hook.mjs for ${id}`);
        assert.ok(host.events[event], `${file} passes ${event}, which host.mjs does not map`);
        // A detached event's requests are not bound by the hook's timeout.
        if (typeof entry.timeout !== "number" || host.events[event] === "toolCall" || host.detachEvents?.includes(event)) continue;
        const budget = host.budgets?.[event];
        assert.ok(budget > 0 && budget <= entry.timeout * 1000 - 3000,
          `${file} ${event}: request budget ${budget}ms, hook timeout ${entry.timeout}s`);
      }
    }
  });

  const fixtures = join(dir, "fixtures");
  if (!existsSync(fixtures)) continue;
  for (const file of readdirSync(fixtures).filter((name) => name.endsWith(".json")).sort()) {
    const path = join(fixtures, file);
    const fixture = JSON.parse(readFileSync(path, "utf8"));
    test(`${directory}: ${fixture.what || file}`, async () => {
      const host = await loadHost(fixture.host || directory);
      const actual = JSON.parse(JSON.stringify(outcome(host, fixtures, fixture)));
      if (UPDATE) {
        writeFileSync(path, `${JSON.stringify({ ...fixture, ...actual }, null, 2)}\n`);
        return;
      }
      for (const [key, value] of Object.entries(actual)) assert.deepEqual(value, fixture[key], `${file}: ${key}`);
    });
  }
}
