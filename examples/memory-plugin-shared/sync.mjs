#!/usr/bin/env node

import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SHARED_DIR = join(ROOT, "examples", "memory-plugin-shared", "lib");
const HARNESS_SHARED_FILES = [
  "credentials.mjs",
  "capture-utils.mjs",
  "session-model.mjs",
  "pending-queue.mjs",
  "debug-log.mjs",
  "setup-wizard.mjs",
  "plugin-config.mjs",
  "recall-compress-core.mjs",
  "recall-core.mjs",
  "retryable.mjs",
  "workspace-peer.mjs",
  "profile-inject.mjs",
  "uri-guard.mjs",
];
const OPENCODE_SHARED_FILES = [...HARNESS_SHARED_FILES, "mcp-proxy-core.mjs", "mcp-proxy-config.mjs", "async-writer.mjs", "batch-send.mjs"];
const DSH_SHARED_FILES = [...HARNESS_SHARED_FILES, "mcp-proxy-core.mjs", "mcp-proxy-config.mjs"];
const AGENT_PLUGINS_SHARED_FILES = [
  "credentials.mjs",
  "debug-log.mjs",
  "mcp-proxy-core.mjs",
  "mcp-proxy-config.mjs",
  "workspace-peer.mjs",
];
const TARGETS = [
  { dir: join(ROOT, "examples", "claude-code-memory-plugin", "scripts", "shared"), files: OPENCODE_SHARED_FILES },
  { dir: join(ROOT, "examples", "codex-memory-plugin", "scripts", "shared"), files: OPENCODE_SHARED_FILES },
  { dir: join(ROOT, "examples", "opencode-plugin", "lib", "shared"), files: OPENCODE_SHARED_FILES },
  { dir: join(ROOT, "examples", "dsh-memory-plugin", "shared"), files: DSH_SHARED_FILES },
  { dir: join(ROOT, "examples", "pi-coding-agent-extension", "shared"), files: HARNESS_SHARED_FILES },
  { dir: join(ROOT, "examples", "zcode-memory-plugin", "scripts", "shared") },
  { dir: join(ROOT, "agent-plugins", "servers", "shared"), files: AGENT_PLUGINS_SHARED_FILES },
];

const GENERATED_HEADER = "// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.\n";

// Skills are copied verbatim — a generated-from banner ahead of the `---`
// frontmatter would break every skill loader.
const SKILLS_DIR = join(ROOT, "examples", "skills");
const SKILL_TARGETS = [
  {
    // Not shipped to openclaw-plugin: its REST tool surface has its own
    // operator skill (openviking-context-database) with different tool names.
    skill: "openviking-memory",
    dirs: [
      join(ROOT, "examples", "codex-memory-plugin", "skills"),
      join(ROOT, "examples", "claude-code-memory-plugin", "skills"),
      join(ROOT, "examples", "cursor-memory-plugin", "skills"),
      join(ROOT, "examples", "dsh-memory-plugin", "skills"),
    ],
  },
];

async function listSharedFiles() {
  const files = await readdir(SHARED_DIR);
  return files.filter((file) => file.endsWith(".mjs")).sort();
}

async function copySharedFile(file, targetDir) {
  await mkdir(targetDir, { recursive: true });
  const source = join(SHARED_DIR, file);
  const target = join(targetDir, file);
  const body = await readFile(source, "utf-8");
  await writeFile(target, `${GENERATED_HEADER}${body}`, "utf-8");
}

async function copySkill(skill, targetDir) {
  const sourceDir = join(SKILLS_DIR, skill);
  for (const file of (await readdir(sourceDir)).sort()) {
    const target = join(targetDir, skill);
    await mkdir(target, { recursive: true });
    await writeFile(join(target, file), await readFile(join(sourceDir, file), "utf-8"), "utf-8");
  }
}

async function main() {
  const allFiles = await listSharedFiles();
  for (const target of TARGETS) {
    const files = target.files ?? allFiles;
    for (const file of files) {
      if (!allFiles.includes(file)) {
        throw new Error(`shared file not found: ${file}`);
      }
      await copySharedFile(file, target.dir);
      process.stdout.write(`synced ${file} -> ${relative(ROOT, target.dir)}\n`);
    }
  }
  for (const { skill, dirs } of SKILL_TARGETS) {
    for (const dir of dirs) {
      await copySkill(skill, dir);
      process.stdout.write(`synced ${skill}/ -> ${relative(ROOT, dir)}\n`);
    }
  }
}

main().catch((err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  process.exit(1);
});
