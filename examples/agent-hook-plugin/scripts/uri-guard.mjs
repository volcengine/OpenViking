#!/usr/bin/env node

/**
 * The tool-call guard as a hook entry of its own: `uri-guard.mjs <host>`.
 * It answers the same as `hook.mjs` does for a host's tool-call event.
 */

import { readFileSync } from "node:fs";

import { toolCall } from "../lib/flow/index.mjs";
import { loadHost } from "../hosts/index.mjs";

const host = await loadHost(process.argv[2] || process.env.OPENVIKING_HOOK_SOURCE || "");
if (host) {
  let payload = {};
  try {
    payload = JSON.parse(readFileSync(0, "utf8").trim() || "{}") || {};
  } catch {
    payload = {};
  }
  // An empty answer prints nothing: every host reads that as "no opinion",
  // and one rejects any key it does not know.
  const value = host.emit("", toolCall(host, payload), payload);
  if (value && Object.keys(value).length > 0) process.stdout.write(`${JSON.stringify(value)}\n`);
}
