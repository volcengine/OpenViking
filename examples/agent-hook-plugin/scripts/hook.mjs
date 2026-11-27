#!/usr/bin/env node

/**
 * The one hook entry: `hook.mjs <event> <host>`. The host module under
 * `hosts/<host>/` says what the event means and how the answer is shaped;
 * `lib/flow` decides what happens.
 */

import { createAgentLogger, loadAgentHookConfig } from "../lib/agent-hook-runtime.mjs";
import { maybeDetach, readHookStdin } from "../lib/async-writer.mjs";
import { mayDetach, runStage } from "../lib/flow/index.mjs";
import { loadHost } from "../hosts/index.mjs";

// A detached worker re-enters this file with no arguments, so the first run
// leaves what it was in the environment.
const event = process.argv[2] || process.env.OPENVIKING_HOOK_EVENT || "";
const hostId = process.argv[3] || process.env.OPENVIKING_HOOK_SOURCE || "";
const host = await loadHost(hostId);

if (!host) {
  process.stderr.write(`openviking: no hook adapter for ${hostId || "?"}\n`);
  process.exit(0);
}

process.env.OPENVIKING_HOOK_EVENT = event;
process.env.OPENVIKING_HOOK_SOURCE = hostId;

let answered = false;
function answer(output = {}, payload = {}) {
  if (answered) return;
  answered = true;
  const value = host.emit(event, output, payload);
  if (typeof value === "string") process.stdout.write(`${value}\n`);
  else if (value) process.stdout.write(`${JSON.stringify(value)}\n`);
}

async function main() {
  const cfg = loadAgentHookConfig(host.id);
  // The write path answers before it works, so the worker is started while
  // stdin is still unread.
  const detach = mayDetach(host, event, cfg);
  const detachCfg = detach === "always" ? { ...cfg, writePathAsync: true } : cfg;
  if (detach && await maybeDetach(detachCfg, { approve: () => answer() })) return;
  let payload;
  try {
    payload = JSON.parse(await readHookStdin() || "{}");
  } catch {
    payload = {};
  }
  if (!payload || typeof payload !== "object") payload = {};
  try {
    answer(await runStage(host, event, payload, { detached: process.env.OV_HOOK_WORKER === "1" }), payload);
  } catch (error) {
    createAgentLogger(host.id, event, cfg).logError("uncaught", error);
    answer({}, payload);
  }
}

await main();
