#!/usr/bin/env node

/**
 * Optional PostToolUse(Read) hook for skill experience recall.
 *
 * Default off. When enabled, it only runs for reads of a SKILL.md file under a
 * skills directory, then injects a small experience block if OpenViking has
 * relevant experience memories.
 */

import { readFileSync } from "node:fs";
import { basename, normalize, sep } from "node:path";
import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { makeFetchJSON } from "./lib/ov-session.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const cfg = loadConfig();
const { log, logError } = createLogger("skill-experience");
const fetchJSON = makeFetchJSON(cfg);

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function approve(additionalContext) {
  const out = { decision: "approve" };
  if (additionalContext) {
    out.hookSpecificOutput = {
      hookEventName: "PostToolUse",
      additionalContext,
    };
  }
  output(out);
}

async function readStdinJson() {
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    return JSON.parse(Buffer.concat(chunks).toString() || "{}");
  } catch {
    return {};
  }
}

function toolName(input) {
  return String(input.tool_name || input.toolName || input.name || input.tool || "").trim();
}

function readPath(input) {
  const candidate =
    input.tool_input?.file_path ||
    input.tool_input?.path ||
    input.toolInput?.file_path ||
    input.toolInput?.path ||
    input.file_path ||
    input.path ||
    input.params?.file_path ||
    input.params?.path ||
    "";
  return String(candidate || "").trim();
}

function isSkillFile(path) {
  if (!path) return false;
  const normalized = normalize(path);
  const parts = normalized.split(sep).filter(Boolean);
  return basename(normalized) === "SKILL.md" && parts.includes("skills");
}

function skillNameFromContent(path) {
  try {
    const raw = readFileSync(path, "utf-8").slice(0, 4096);
    const fm = raw.match(/^---\s*\n([\s\S]*?)\n---/);
    const head = fm ? fm[1] : raw;
    const named = head.match(/^\s*name\s*:\s*['"]?([^'"\n]+)['"]?\s*$/im);
    if (named?.[1]) return named[1].trim();
    const title = raw.match(/^\s*#\s+(.+?)\s*$/m);
    if (title?.[1]) return title[1].trim();
  } catch {
    // The Read tool already succeeded; inability to re-read locally should only
    // disable this optional enhancement.
  }
  return basename(path.replace(/\/SKILL\.md$/, ""));
}

function clampScore(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 0;
  return Math.max(0, Math.min(1, num));
}

async function findExperiences(query) {
  const res = await fetchJSON("/api/v1/search/find", {
    method: "POST",
    body: JSON.stringify({
      query,
      target_uri: "viking://user/memories/experiences",
      context_type: "memory",
      limit: cfg.skillExperienceLimit,
      score_threshold: cfg.scoreThreshold,
    }),
  });
  if (!res.ok) return [];
  return (res.result?.memories || [])
    .filter((item) => /\/memories\/experiences\//.test(String(item.uri || "")))
    .slice(0, cfg.skillExperienceLimit);
}

function buildContext(skillName, items) {
  if (items.length === 0) return "";
  const lines = [
    '<openviking-context source="skill-experience" format="experience-digest">',
    `Relevant prior experience for skill: ${skillName}`,
  ];
  for (const item of items) {
    const score = Math.round(clampScore(item.score) * 100);
    const text = String(item.abstract || item.overview || item.uri || "").replace(/\s+/g, " ").trim();
    lines.push(`- [experience ${score}%] ${text} (${item.uri})`);
  }
  lines.push("Use these as operational guidance, not user facts.");
  lines.push("</openviking-context>");
  return lines.join("\n");
}

async function main() {
  if (!cfg.skillExperience) {
    log("skip", { reason: "disabled" });
    approve();
    return;
  }

  const input = await readStdinJson();
  const name = toolName(input);
  const path = readPath(input);
  if (name && name !== "Read") {
    log("skip", { reason: "not_read", toolName: name });
    approve();
    return;
  }
  if (!isSkillFile(path)) {
    log("skip", { reason: "not_skill_file", path });
    approve();
    return;
  }

  const skillName = skillNameFromContent(path);
  const query = `skill ${skillName} usage experience`;
  const items = await findExperiences(query);
  const context = buildContext(skillName, items);
  log("done", { skillName, count: items.length });
  approve(context);
}

main().catch((err) => {
  logError("uncaught", err);
  approve();
});
