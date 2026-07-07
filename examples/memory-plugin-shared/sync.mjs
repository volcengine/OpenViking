#!/usr/bin/env node

import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SHARED_DIR = join(ROOT, "examples", "memory-plugin-shared", "lib");
const TARGETS = [
  join(ROOT, "examples", "claude-code-memory-plugin", "scripts", "shared"),
  join(ROOT, "examples", "codex-memory-plugin", "scripts", "shared"),
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
  const files = await listSharedFiles();
  for (const targetDir of TARGETS) {
    for (const file of files) {
      await copySharedFile(file, targetDir);
      process.stdout.write(`synced ${file} -> ${relative(ROOT, targetDir)}\n`);
    }
  }
}

main().catch((err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  process.exit(1);
});
