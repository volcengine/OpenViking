import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SHARED_DIR = join(ROOT, "examples", "memory-plugin-shared", "lib");
const TARGETS = [
  join(ROOT, "examples", "claude-code-memory-plugin", "scripts", "shared"),
  join(ROOT, "examples", "codex-memory-plugin", "scripts", "shared"),
];
const GENERATED_HEADER = "// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.\n";

test("vendored shared modules are synchronized", async () => {
  const files = (await readdir(SHARED_DIR)).filter((file) => file.endsWith(".mjs")).sort();
  assert.ok(files.length > 0, "expected shared modules");

  for (const targetDir of TARGETS) {
    for (const file of files) {
      const expected = `${GENERATED_HEADER}${await readFile(join(SHARED_DIR, file), "utf-8")}`;
      const actual = await readFile(join(targetDir, file), "utf-8");
      assert.equal(
        actual,
        expected,
        `${relative(ROOT, join(targetDir, file))} is out of sync; run node examples/memory-plugin-shared/sync.mjs`,
      );
    }
  }
});
