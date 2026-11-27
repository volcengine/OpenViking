#!/usr/bin/env node

/**
 * LoCoMo benchmark capture hook: `capture.mjs <Event>`.
 *
 * The plugin's Claude Code host on the plugin's own flow, with one difference:
 * each captured message carries a `created_at` taken from the LoCoMo
 * session_time stamp in the conversation (e.g. "[group chat conversation:
 * 4:02 pm on 12 February, 2023]"), mirroring the SDK ingest path
 * (import_to_ov.py). The server derives event archive dates from
 * `created_at`; without the stamp every auto-captured LoCoMo conversation
 * would land on the ingest day. A transcript with no stamp is captured as the
 * plugin captures it.
 */

import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const pluginDir = process.env.OPENVIKING_PLUGIN_DIR;
if (!pluginDir) {
  process.stderr.write("capture: OPENVIKING_PLUGIN_DIR not set\n");
  process.stdout.write(`${JSON.stringify({ decision: "approve" })}\n`);
  process.exit(0);
}
const plugin = (path) => pathToFileURL(`${pluginDir}/${path}`).href;
const { runStage } = await import(plugin("lib/flow/index.mjs"));
const { default: claudeCode } = await import(plugin("hosts/claude-code/host.mjs"));

const MONTHS = {
  january: 0, february: 1, march: 2, april: 3, may: 4, june: 5,
  july: 6, august: 7, september: 8, october: 9, november: 10, december: 11,
};

// Matches LoCoMo session_X_date_time strings like "7:55 pm on 9 June, 2023"
// whether wrapped in `(...)` (v12 prompt) or `[...]` (v14 group-chat header)
// or appearing bare. Boundaries are loose because LoCoMo prompts vary.
const LOCOMO_TS_RE =
  /(\d{1,2}):(\d{2})\s*([ap]m)\s+on\s+(\d{1,2})\s+([A-Za-z]+),\s*(\d{4})/i;

function parseLocomoStamp(text) {
  const m = String(text || "").match(LOCOMO_TS_RE);
  if (!m) return null;
  let h = parseInt(m[1], 10);
  const ampm = m[3].toLowerCase();
  if (ampm === "pm" && h < 12) h += 12;
  if (ampm === "am" && h === 12) h = 0;
  const mo = MONTHS[m[5].toLowerCase()];
  if (mo === undefined) return null;
  // UTC, so the host's time zone does not move the day boundary.
  return new Date(Date.UTC(parseInt(m[6], 10), mo, parseInt(m[4], 10), h, parseInt(m[2], 10), 0));
}

const host = {
  ...claudeCode,
  // The benchmark reads the server right after each hook returns.
  canDetach: false,
  decode(input) {
    const decoded = claudeCode.decode(input);
    const base = parseLocomoStamp(input.text);
    if (!base) return decoded;
    // One second apart, counted from the start of the transcript, so messages
    // keep their order across captures.
    const first = Number(input.cursor?.capturedTurnCount) || 0;
    return {
      ...decoded,
      turns: decoded.turns.map((turn, index) => ({
        ...turn,
        createdAt: new Date(base.getTime() + (first + index) * 1000).toISOString(),
      })),
    };
  },
};

const event = process.argv[2] || "";
let payload = {};
try {
  payload = JSON.parse(readFileSync(0, "utf8").trim() || "{}") || {};
} catch {
  payload = {};
}
const value = host.emit(event, await runStage(host, event, payload), payload);
if (value) process.stdout.write(`${JSON.stringify(value)}\n`);
