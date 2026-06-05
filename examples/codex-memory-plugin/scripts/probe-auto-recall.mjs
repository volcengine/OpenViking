#!/usr/bin/env node

/**
 * Live regression probe for Codex auto-recall.
 *
 * Runs auto-recall.mjs with a small set of prompts and validates whether the
 * hook injects context. This intentionally talks to the configured OpenViking
 * server, so it is a smoke probe rather than a unit test.
 *
 * Optional env:
 *   OPENVIKING_RECALL_PROBE_CASES='[{"name":"...","prompt":"...","expect":"none"},{"name":"...","prompt":"...","contains":["foo"]}]'
 */

import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const autoRecallPath = join(here, "auto-recall.mjs");

const DEFAULT_CASES = [
  {
    name: "russian vague prompt stays quiet",
    prompt: "Посмотри что там было и подскажи",
    expect: "none",
  },
  {
    name: "codex openviking diagnostic stays quiet",
    prompt: "я установил openviking и подключил его к codex он инжектит нерелевантный контекст почему",
    expect: "none",
  },
  {
    name: "russian Temporal/Windmill recall",
    prompt: "Напомни мой вывод про Temporal vs Windmill и что я хотел попробовать первым",
    contains: ["Temporal", "Windmill"],
  },
  {
    name: "russian Second Brain policy recall",
    prompt: "Что я решил про Second Brain Write Policy и кто должен быть semantic authority?",
    contains: ["Second Brain", "semantic authority"],
  },
];

function loadCases() {
  const raw = process.env.OPENVIKING_RECALL_PROBE_CASES;
  if (!raw) return DEFAULT_CASES;
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length > 0) return parsed;
  } catch (err) {
    throw new Error(`Invalid OPENVIKING_RECALL_PROBE_CASES JSON: ${err.message}`);
  }
  throw new Error("OPENVIKING_RECALL_PROBE_CASES must be a non-empty JSON array");
}

function runAutoRecall(prompt, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [autoRecallPath], {
      env: { ...process.env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill("SIGTERM");
      reject(new Error(`auto-recall timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(err);
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(`auto-recall exited ${code}: ${stderr || stdout}`));
        return;
      }
      resolve(stdout.trim());
    });
    child.stdin.end(JSON.stringify({ prompt }));
  });
}

function getInjectedContext(raw) {
  if (!raw || raw === "{}") return "";
  const parsed = JSON.parse(raw);
  return parsed?.hookSpecificOutput?.additionalContext || "";
}

function validateCase(testCase, injected) {
  if (testCase.expect === "none") {
    if (injected) {
      throw new Error(`expected no injected context, got ${injected.length} chars`);
    }
    return;
  }
  const contains = Array.isArray(testCase.contains) ? testCase.contains : [];
  for (const needle of contains) {
    if (!injected.includes(needle)) {
      throw new Error(`expected injected context to contain ${JSON.stringify(needle)}`);
    }
  }
  if (contains.length > 0 && !injected) {
    throw new Error("expected injected context, got none");
  }
}

async function main() {
  const cases = loadCases();
  let failed = 0;
  for (const testCase of cases) {
    try {
      const raw = await runAutoRecall(testCase.prompt);
      const injected = getInjectedContext(raw);
      validateCase(testCase, injected);
      const status = injected ? `${injected.length} injected chars` : "no injection";
      process.stdout.write(`ok - ${testCase.name}: ${status}\n`);
    } catch (err) {
      failed += 1;
      process.stdout.write(`not ok - ${testCase.name}: ${err.message}\n`);
    }
  }
  if (failed > 0) process.exit(1);
}

main().catch((err) => {
  process.stderr.write(`${err.stack || err.message}\n`);
  process.exit(1);
});
