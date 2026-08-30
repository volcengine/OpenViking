#!/usr/bin/env node

/**
 * PostToolUse hook for local-only Git repositories.
 *
 * The parent hook immediately returns an empty Codex response when async
 * writes are enabled; its detached worker archives committed HEAD and submits
 * the snapshot without delaying the completed Git tool invocation.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { maybeDetach, readHookStdin } from "./shared/async-writer.mjs";
import { syncRepositoryFromHook } from "./shared/repository-sync.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("repository-sync", cfg);

function output(value = {}) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

async function main() {
  if (process.env.OPENVIKING_GIT_LOCAL_ENABLED === "0") {
    output({});
    return;
  }
  if (await maybeDetach(cfg, { approve: () => output({}) })) return;

  let input;
  try {
    const raw = await readHookStdin();
    input = raw.trim() ? JSON.parse(raw) : {};
  } catch {
    output({});
    return;
  }

  const result = await syncRepositoryFromHook(input, { credentials: cfg }).catch((error) => {
    logError("repository-sync", error);
    return { status: "error", error: error?.message || String(error) };
  });
  log("repository-sync", {
    status: result?.status,
    reason: result?.reason,
    code_status: result?.code?.status,
    code_task_id: result?.code?.result?.task_id,
    wiki_status: result?.wiki?.status,
    wiki_task_id: result?.wiki?.result?.task_id,
  });
  if (process.env.OV_HOOK_WORKER !== "1") output({});
}

await main();
