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
  "recall-core.mjs",
  "workspace-peer.mjs",
  "profile-inject.mjs",
  "uri-guard.mjs",
];
const OPENCODE_SHARED_FILES = [...HARNESS_SHARED_FILES, "mcp-proxy-core.mjs", "async-writer.mjs", "batch-send.mjs"];
const TARGETS = [
  { dir: join(ROOT, "examples", "claude-code-memory-plugin", "scripts", "shared"), files: OPENCODE_SHARED_FILES },
  { dir: join(ROOT, "examples", "codex-memory-plugin", "scripts", "shared"), files: OPENCODE_SHARED_FILES },
  { dir: join(ROOT, "examples", "opencode-plugin", "lib", "shared"), files: OPENCODE_SHARED_FILES },
  { dir: join(ROOT, "examples", "pi-coding-agent-extension", "shared"), files: HARNESS_SHARED_FILES },
];

const GENERATED_HEADER = "// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.\n";

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
}

main().catch((err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  process.exit(1);
});
