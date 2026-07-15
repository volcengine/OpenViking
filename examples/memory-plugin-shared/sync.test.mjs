import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import test from "node:test";
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

test("vendored shared modules are synchronized", async () => {
  const files = (await readdir(SHARED_DIR)).filter((file) => file.endsWith(".mjs")).sort();
  assert.ok(files.length > 0, "expected shared modules");

  for (const target of TARGETS) {
    const targetFiles = target.files ?? files;
    for (const file of files) {
      if (!targetFiles.includes(file)) continue;
      const expected = `${GENERATED_HEADER}${await readFile(join(SHARED_DIR, file), "utf-8")}`;
      const actual = await readFile(join(target.dir, file), "utf-8");
      assert.equal(
        actual,
        expected,
        `${relative(ROOT, join(target.dir, file))} is out of sync; run node examples/memory-plugin-shared/sync.mjs`,
      );
    }
  }
});
