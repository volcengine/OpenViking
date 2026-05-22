#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";

function fail(message) {
  console.error(`[sccs-bench] ${message}`);
  process.exit(1);
}

function nowIso() {
  return new Date().toISOString();
}

function asNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function numOrZero(value) {
  return asNumber(value) ?? 0;
}

function percentile(sortedValues, p) {
  if (!sortedValues.length) {
    return 0;
  }
  const rank = (p / 100) * (sortedValues.length - 1);
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  if (lo === hi) {
    return sortedValues[lo];
  }
  const weight = rank - lo;
  return sortedValues[lo] * (1 - weight) + sortedValues[hi] * weight;
}

function toCsv(rows, headers) {
  const escape = (value) => {
    const raw =
      value === undefined || value === null
        ? ""
        : typeof value === "string"
          ? value
          : JSON.stringify(value);
    if (/[",\n]/.test(raw)) {
      return `"${raw.replace(/"/g, "\"\"")}"`;
    }
    return raw;
  };
  const lines = [headers.join(",")];
  for (const row of rows) {
    lines.push(headers.map((h) => escape(row[h])).join(","));
  }
  return `${lines.join("\n")}\n`;
}

function parseArgs(argv) {
  const args = {
    cmd: "run",
    openclawBin: "openclaw",
    prompts: "",
    outDir: path.resolve(process.cwd(), "bench-results"),
    label: "run",
    agent: "main",
    to: "+15555550123",
    sessionId: `sccs-bench-${Date.now()}`,
    timeoutSec: 600,
    stateDir: process.env.OPENCLAW_STATE_DIR
      ? path.resolve(process.env.OPENCLAW_STATE_DIR)
      : path.join(os.homedir(), ".openclaw"),
    continueOnError: false,
    baseline: "",
    candidate: "",
    out: "",
  };

  const positional = [];
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) {
      positional.push(token);
      continue;
    }
    const [key, maybeValue] = token.split("=", 2);
    const nextValue = maybeValue ?? argv[i + 1];
    const takeNext = maybeValue === undefined;
    const set = (field, valueRequired = true) => {
      if (valueRequired && (nextValue === undefined || nextValue.startsWith("--"))) {
        fail(`missing value for ${key}`);
      }
      if (takeNext && valueRequired) {
        i += 1;
      }
      args[field] = valueRequired ? nextValue : true;
    };

    switch (key) {
      case "--help":
        args.cmd = "help";
        break;
      case "--openclaw":
        set("openclawBin");
        break;
      case "--prompts":
        set("prompts");
        break;
      case "--out-dir":
        set("outDir");
        break;
      case "--label":
        set("label");
        break;
      case "--agent":
        set("agent");
        break;
      case "--to":
        set("to");
        break;
      case "--session-id":
        set("sessionId");
        break;
      case "--timeout-sec":
        set("timeoutSec");
        break;
      case "--state-dir":
        set("stateDir");
        break;
      case "--continue-on-error":
        set("continueOnError", false);
        break;
      case "--baseline":
        set("baseline");
        break;
      case "--candidate":
        set("candidate");
        break;
      case "--out":
        set("out");
        break;
      default:
        fail(`unknown flag: ${key}`);
    }
  }

  if (positional.length > 0) {
    args.cmd = positional[0];
  }
  args.outDir = path.resolve(args.outDir);
  args.stateDir = path.resolve(args.stateDir);
  args.timeoutSec = Number.parseInt(String(args.timeoutSec), 10);
  if (!Number.isFinite(args.timeoutSec) || args.timeoutSec <= 0) {
    fail("--timeout-sec must be a positive integer");
  }
  return args;
}

function loadPrompts(promptsPath) {
  if (!promptsPath) {
    fail("run mode requires --prompts");
  }
  const resolved = path.resolve(promptsPath);
  if (!fs.existsSync(resolved)) {
    fail(`prompts file not found: ${resolved}`);
  }
  const raw = fs.readFileSync(resolved, "utf8");
  let prompts = [];
  if (resolved.endsWith(".json")) {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      fail("prompts JSON must be an array");
    }
    prompts = parsed.map((item, index) => {
      if (typeof item === "string") {
        return { id: `turn-${index + 1}`, prompt: item };
      }
      if (
        item &&
        typeof item === "object" &&
        typeof item.prompt === "string" &&
        item.prompt.trim()
      ) {
        return {
          id: typeof item.id === "string" && item.id.trim() ? item.id.trim() : `turn-${index + 1}`,
          prompt: item.prompt,
        };
      }
      fail(`invalid prompt entry at index ${index}`);
    });
  } else {
    prompts = raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => ({ id: `turn-${index + 1}`, prompt: line }));
  }

  if (prompts.length === 0) {
    fail("no prompts found");
  }
  return { prompts, path: resolved };
}

function loadSessionStore(stateDir, agentId) {
  const storePath = path.join(stateDir, "agents", agentId, "sessions", "sessions.json");
  if (!fs.existsSync(storePath)) {
    return { storePath, store: {} };
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(storePath, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { storePath, store: {} };
    }
    return { storePath, store: parsed };
  } catch {
    return { storePath, store: {} };
  }
}

function pickSessionEntryById(store, sessionId) {
  const matches = Object.entries(store)
    .filter(([, entry]) => entry)
    .map(([key, entry]) => ({ key, entry }));
  if (matches.length === 0) {
    return undefined;
  }
  matches.sort((a, b) => (asNumber(b.entry.updatedAt) ?? 0) - (asNumber(a.entry.updatedAt) ?? 0));
  return matches[0];
}

function extractUsageInfo(parsed) {
  const root = parsed && typeof parsed === "object" ? parsed : {};
  const result = root.result && typeof root.result === "object" ? root.result : null;
  const metaCandidate =
    (result?.meta && typeof result.meta === "object" ? result.meta : null) ||
    (root.meta && typeof root.meta === "object" ? root.meta : null);
  const agentMeta =
    metaCandidate?.agentMeta && typeof metaCandidate.agentMeta === "object"
      ? metaCandidate.agentMeta
      : null;
  const usage = agentMeta?.usage && typeof agentMeta.usage === "object" ? agentMeta.usage : null;
  const readNumber = (obj, keys) => {
    if (!obj || typeof obj !== "object") {
      return undefined;
    }
    for (const key of keys) {
      const value = asNumber(obj[key]);
      if (value !== undefined) {
        return value;
      }
    }
    return undefined;
  };

  const inputTokens = readNumber(usage, [
    "input",
    "inputTokens",
    "input_tokens",
    "promptTokens",
    "prompt_tokens",
  ]);
  const outputTokens = readNumber(usage, ["output", "outputTokens", "output_tokens", "completion_tokens"]);
  const cacheRead = readNumber(usage, ["cacheRead", "cache_read", "cache_read_input_tokens"]);
  const cacheWrite = readNumber(usage, ["cacheWrite", "cache_write", "cache_creation_input_tokens"]);
  const promptTokens =
    asNumber(agentMeta?.promptTokens) ??
    (inputTokens !== undefined || cacheRead !== undefined || cacheWrite !== undefined
      ? (inputTokens ?? 0) + (cacheRead ?? 0) + (cacheWrite ?? 0)
      : undefined);
  const totalUsageTokens =
    readNumber(usage, ["total", "totalTokens", "total_tokens"]) ??
    (inputTokens !== undefined ||
    outputTokens !== undefined ||
    cacheRead !== undefined ||
    cacheWrite !== undefined
      ? (inputTokens ?? 0) + (outputTokens ?? 0) + (cacheRead ?? 0) + (cacheWrite ?? 0)
      : undefined);
  const compactionCount = asNumber(agentMeta?.compactionCount);
  const sessionId =
    typeof agentMeta?.sessionId === "string" && agentMeta.sessionId.trim()
      ? agentMeta.sessionId.trim()
      : undefined;

  return {
    inputTokens,
    outputTokens,
    cacheRead,
    cacheWrite,
    promptTokens,
    totalUsageTokens,
    compactionCount,
    sessionId,
  };
}

function extractReplyInfo(resultPayload) {
  const payloads = Array.isArray(resultPayload?.payloads) ? resultPayload.payloads : [];
  const textParts = [];
  let mediaCount = 0;
  for (const payload of payloads) {
    if (payload && typeof payload.text === "string" && payload.text.trim()) {
      textParts.push(payload.text);
    }
    if (payload && Array.isArray(payload.mediaUrls)) {
      mediaCount += payload.mediaUrls.length;
    }
    if (payload && typeof payload.mediaUrl === "string" && payload.mediaUrl.trim()) {
      mediaCount += 1;
    }
  }
  const text = textParts.join("\n").trim();
  return {
    textChars: text.length,
    mediaCount,
    payloadCount: payloads.length,
  };
}

function computeSummary(rows) {
  const durations = rows.map((r) => r.durationMs).filter((v) => typeof v === "number").sort((a, b) => a - b);
  const sum = (field) => rows.reduce((acc, row) => acc + (numOrZero(row[field]) || 0), 0);
  const count = rows.length;
  const totals = {
    inputTokens: sum("inputTokens"),
    outputTokens: sum("outputTokens"),
    cacheRead: sum("cacheRead"),
    cacheWrite: sum("cacheWrite"),
    promptTokens: sum("promptTokens"),
    totalUsageTokens: sum("totalUsageTokens"),
    replyChars: sum("replyChars"),
  };
  const avg = (value) => (count > 0 ? value / count : 0);
  const promptBase = totals.inputTokens + totals.cacheRead + totals.cacheWrite;
  const cacheReadShare = promptBase > 0 ? totals.cacheRead / promptBase : 0;
  return {
    turns: count,
    totals,
    averages: {
      inputTokens: avg(totals.inputTokens),
      outputTokens: avg(totals.outputTokens),
      cacheRead: avg(totals.cacheRead),
      cacheWrite: avg(totals.cacheWrite),
      promptTokens: avg(totals.promptTokens),
      totalUsageTokens: avg(totals.totalUsageTokens),
      durationMs: avg(sum("durationMs")),
      replyChars: avg(totals.replyChars),
    },
    latencyMs: {
      p50: percentile(durations, 50),
      p90: percentile(durations, 90),
      max: durations.length ? durations[durations.length - 1] : 0,
    },
    cacheReadShare,
    compactionTriggeredTurns: rows.filter((r) => numOrZero(r.compactionCountDelta) > 0).length,
  };
}

function moveSessionJsonlFiles(stateDir, agentId, outDir, sessionStorePath) {
  const sessionsDir = path.join(stateDir, "agents", agentId, "sessions");
  if (!fs.existsSync(sessionsDir)) {
    return 0;
  }
  const names = fs.readdirSync(sessionsDir).filter((name) => name.endsWith(".jsonl"));
  if (names.length === 0) {
    return 0;
  }
  fs.mkdirSync(outDir, { recursive: true });
  let moved = 0;
  for (const name of names) {
    const source = path.join(sessionsDir, name);
    // const target = path.join(outDir, name);
    const target = sessionStorePath;
    try {
      fs.renameSync(source, target);
      moved += 1;
    } catch (err) {
      if (err && typeof err === "object" && "code" in err && err.code === "EXDEV") {
        fs.copyFileSync(source, target);
        fs.unlinkSync(source);
        moved += 1;
      } else {
        throw err;
      }
    }
  }
  return moved;
}

function runBench(args) {
  const loaded = loadPrompts(args.prompts);
  const startedAt = nowIso();
  const rows = [];
  let prevCompactionCount;
  let failures = 0;
  fs.mkdirSync(args.outDir, { recursive: true });

  console.log(`[sccs-bench] started at ${startedAt}`);
  console.log(`[sccs-bench] prompts: ${loaded.path} (${loaded.prompts.length} turns)`);
  console.log(`[sccs-bench] target: agent=${args.agent} to=${args.to} sessionId=${args.sessionId}`);

  for (let i = 0; i < loaded.prompts.length; i += 1) {
    const turn = loaded.prompts[i];
    const cmdArgs = [
      "agent",
      "--agent",
      String(args.agent),
      // "--to",
      // String(args.to),
      // "--session-id",
      // String(args.sessionId),
      "--timeout",
      String(args.timeoutSec),
      "--message",
      turn.prompt,
      "--json",
    ];

    const begin = Date.now();
    const result = spawnSync(String(args.openclawBin), cmdArgs, {
      encoding: "utf8",
      maxBuffer: 10 * 1024 * 1024,
    });
    const durationMs = Date.now() - begin;

    let parsed;
    let errorMessage = "";
    if (result.status !== 0) {
      errorMessage = `exit=${result.status} stderr=${(result.stderr || "").trim()}`;
    } else {
      try {
        parsed = JSON.parse((result.stdout || "").trim() || "{}");
      } catch (err) {
        errorMessage = `json parse failed: ${String(err)}`;
      }
    }

    const usageInfo = extractUsageInfo(parsed);
    console.log(`[sccs-bench] usageInfo: ${JSON.stringify(usageInfo)}`);
    const { storePath, store } = loadSessionStore(args.stateDir, args.agent);
    const match =
      pickSessionEntryById(store, usageInfo.sessionId ?? args.sessionId) ??
      pickSessionEntryById(store, args.sessionId);
    const entry = match?.entry ?? {};

    const inputTokens = usageInfo.inputTokens ?? numOrZero(entry.inputTokens);
    const outputTokens = usageInfo.outputTokens ?? numOrZero(entry.outputTokens);
    const cacheRead = usageInfo.cacheRead ?? numOrZero(entry.cacheRead);
    const cacheWrite = usageInfo.cacheWrite ?? numOrZero(entry.cacheWrite);
    const promptTokens = usageInfo.promptTokens ?? inputTokens + cacheRead + cacheWrite;
    const totalUsageTokens =
      usageInfo.totalUsageTokens ?? inputTokens + outputTokens + cacheRead + cacheWrite;
    const compactionCount = usageInfo.compactionCount ?? numOrZero(entry.compactionCount);
    const compactionCountDelta =
      prevCompactionCount === undefined ? 0 : Math.max(0, compactionCount - prevCompactionCount);
    prevCompactionCount = compactionCount;
    const replyInfo = extractReplyInfo(parsed?.result ?? parsed);

    const row = {
      turn: i + 1,
      turnId: turn.id,
      prompt: turn.prompt,
      status: parsed?.status ?? (errorMessage ? "error" : "unknown"),
      runId: parsed?.runId ?? "",
      durationMs,
      sessionStorePath: storePath,
      sessionKey: match?.key ?? "",
      sessionId: usageInfo.sessionId ?? args.sessionId,
      inputTokens,
      outputTokens,
      cacheRead,
      cacheWrite,
      promptTokens,
      totalUsageTokens,
      contextSnapshotTokens: asNumber(entry.totalTokens) ?? null,
      contextTokensLimit: asNumber(entry.contextTokens) ?? null,
      totalTokensFresh: entry.totalTokensFresh !== false,
      compactionCount,
      compactionCountDelta,
      replyChars: replyInfo.textChars,
      replyPayloadCount: replyInfo.payloadCount,
      replyMediaCount: replyInfo.mediaCount,
      error: errorMessage,
    };
    rows.push(row);

    const basic = `turn ${i + 1}/${loaded.prompts.length} status=${row.status} dur=${durationMs}ms in=${inputTokens} out=${outputTokens} cacheR=${cacheRead}`;
    if (errorMessage) {
      failures += 1;
      console.error(`[sccs-bench] ${basic} error=${errorMessage}`);
      if (!args.continueOnError) {
        break;
      }
    } else {
      console.log(`[sccs-bench] ${basic}`);
    }
  }

  const endedAt = nowIso();
  const summary = computeSummary(rows);
  const report = {
    metadata: {
      label: args.label,
      startedAt,
      endedAt,
      promptsPath: loaded.path,
      openclawBin: args.openclawBin,
      agent: args.agent,
      to: args.to,
      sessionId: args.sessionId,
      stateDir: args.stateDir,
      failures,
      turnsExecuted: rows.length,
    },
    summary,
    rows,
  };

  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const baseName = `${args.label}-${stamp}`;
  const jsonPath = path.join(args.outDir, `${baseName}.json`);
  const csvPath = path.join(args.outDir, `${baseName}.csv`);
  const sessionStorePath = path.join(args.outDir, `${baseName}-sessions.json`);

  fs.writeFileSync(jsonPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  const headers = [
    "turn",
    "turnId",
    "status",
    "runId",
    "durationMs",
    "inputTokens",
    "outputTokens",
    "cacheRead",
    "cacheWrite",
    "promptTokens",
    "totalUsageTokens",
    "contextSnapshotTokens",
    "contextTokensLimit",
    "totalTokensFresh",
    "compactionCount",
    "compactionCountDelta",
    "replyChars",
    "replyPayloadCount",
    "replyMediaCount",
    "sessionKey",
    "sessionId",
    "error",
  ];
  fs.writeFileSync(csvPath, toCsv(rows, headers), "utf8");
  const movedSessions = moveSessionJsonlFiles(args.stateDir, args.agent, args.outDir, sessionStorePath);
  if (movedSessions > 0) {
    console.log(`[sccs-bench] archived ${movedSessions} session jsonl file(s) to ${args.outDir}`);
  }

  console.log(`[sccs-bench] finished at ${endedAt}`);
  console.log(`[sccs-bench] report json: ${jsonPath}`);
  console.log(`[sccs-bench] report csv : ${csvPath}`);
  console.log(
    `[sccs-bench] totals: input=${summary.totals.inputTokens} output=${summary.totals.outputTokens} cacheRead=${summary.totals.cacheRead} cacheWrite=${summary.totals.cacheWrite} usageTotal=${summary.totals.totalUsageTokens}`,
  );
}

function ratioDiff(base, cand) {
  if (base === 0) {
    return null;
  }
  return (cand - base) / base;
}

function compareReports(args) {
  if (!args.baseline || !args.candidate) {
    fail("compare mode requires --baseline and --candidate");
  }
  const basePath = path.resolve(args.baseline);
  const candPath = path.resolve(args.candidate);
  if (!fs.existsSync(basePath) || !fs.existsSync(candPath)) {
    fail("baseline/candidate report file not found");
  }
  const base = JSON.parse(fs.readFileSync(basePath, "utf8"));
  const cand = JSON.parse(fs.readFileSync(candPath, "utf8"));

  const pick = (obj, pathExpr) =>
    pathExpr.split(".").reduce((acc, key) => (acc && key in acc ? acc[key] : undefined), obj);
  const metrics = [
    "summary.totals.inputTokens",
    "summary.totals.outputTokens",
    "summary.totals.cacheRead",
    "summary.totals.cacheWrite",
    "summary.totals.totalUsageTokens",
    "summary.averages.durationMs",
    "summary.latencyMs.p50",
    "summary.latencyMs.p90",
    "summary.compactionTriggeredTurns",
  ];
  const rows = metrics.map((name) => {
    const baseline = numOrZero(pick(base, name));
    const candidate = numOrZero(pick(cand, name));
    const diff = candidate - baseline;
    const pct = ratioDiff(baseline, candidate);
    return {
      metric: name,
      baseline,
      candidate,
      diff,
      diffPercent: pct,
    };
  });

  const report = {
    comparedAt: nowIso(),
    baseline: {
      path: basePath,
      label: base?.metadata?.label ?? "baseline",
      turns: base?.summary?.turns ?? 0,
    },
    candidate: {
      path: candPath,
      label: cand?.metadata?.label ?? "candidate",
      turns: cand?.summary?.turns ?? 0,
    },
    metrics: rows,
  };

  const outPath = args.out
    ? path.resolve(args.out)
    : path.join(
        path.dirname(candPath),
        `compare-${Date.now()}.json`,
      );
  fs.writeFileSync(outPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");

  console.log(`[sccs-bench] compare report: ${outPath}`);
  for (const row of rows) {
    const pctText =
      row.diffPercent === null ? "n/a" : `${(row.diffPercent * 100).toFixed(2)}%`;
    console.log(
      `[sccs-bench] ${row.metric}: baseline=${row.baseline} candidate=${row.candidate} diff=${row.diff} (${pctText})`,
    );
  }
}

function printHelp() {
  console.log(`Usage:
  node bench/run-sccs-bench.mjs run \\
    --prompts bench/prompts-realistic-24.json \\
    --label baseline --agent main --to +15555550123 --session-id bench-baseline

  node bench/run-sccs-bench.mjs compare \\
    --baseline bench-results/baseline.json \\
    --candidate bench-results/sccs.json

Run options:
  --openclaw <bin>       openclaw binary path (default: openclaw)
  --prompts <file>       prompts file (.json array or .txt line-by-line)
  --out-dir <dir>        output directory (default: ./bench-results)
  --label <name>         run label for output files
  --agent <id>           agent id (default: main)
  --to <E.164>           fixed sender/recipient for stable session key
  --session-id <id>      fixed session id for this benchmark
  --timeout-sec <sec>    agent timeout seconds (default: 600)
  --state-dir <dir>      OpenClaw state dir (default: ~/.openclaw)
  --continue-on-error    continue remaining turns on failures

Compare options:
  --baseline <json>      baseline report path
  --candidate <json>     candidate report path
  --out <json>           output path for compare report
`);
}

const args = parseArgs(process.argv.slice(2));
if (args.cmd === "help" || args.cmd === "--help" || args.cmd === "-h") {
  printHelp();
  process.exit(0);
}
if (args.cmd === "run") {
  runBench(args);
  process.exit(0);
}
if (args.cmd === "compare") {
  compareReports(args);
  process.exit(0);
}
fail(`unknown command: ${args.cmd}`);
