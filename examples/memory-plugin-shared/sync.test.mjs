import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { join, relative } from "node:path";
import test from "node:test";

// Imported, not re-declared: the copies kept here had drifted from sync.mjs
// (dsh, opencode and agent-plugins were all missing modules the sync actually
// ships), so a stale vendored file went unnoticed by this very test.
import { GENERATED_HEADER, ROOT, SHARED_DIR, SKILLS_DIR, SKILL_TARGETS, TARGETS } from "./sync.mjs";

test("vendored shared modules are synchronized", async () => {
  const files = (await readdir(SHARED_DIR)).filter((file) => file.endsWith(".mjs")).sort();
  assert.ok(files.length > 0, "expected shared modules");

  for (const target of TARGETS) {
    const targetFiles = target.files ?? files;
    for (const file of targetFiles) {
      assert.ok(files.includes(file), `${file} is listed for ${relative(ROOT, target.dir)} but missing from lib/`);
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

// A module that lib/ ships but no target lists is dead weight nobody consumes;
// one that a target dir holds but no list names is an orphan the sync will
// never refresh again. Both drift silently without this.
test("every shared module is claimed by a target, and no target holds an orphan", async () => {
  const files = (await readdir(SHARED_DIR)).filter((file) => file.endsWith(".mjs")).sort();
  const claimed = new Set(TARGETS.flatMap((target) => target.files ?? files));
  assert.deepEqual(
    files.filter((file) => !claimed.has(file)),
    [],
    "shared modules that no sync target ships",
  );

  for (const target of TARGETS) {
    const expected = new Set(target.files ?? files);
    const orphans = [];
    for (const file of (await readdir(target.dir)).filter((name) => name.endsWith(".mjs")).sort()) {
      if (expected.has(file)) continue;
      // A target dir may also hold plugin-local modules; only a file still
      // carrying the banner is a copy the sync has stopped refreshing.
      const body = await readFile(join(target.dir, file), "utf-8");
      if (body.startsWith(GENERATED_HEADER)) orphans.push(file);
    }
    assert.deepEqual(
      orphans,
      [],
      `${relative(ROOT, target.dir)} holds vendored modules that sync.mjs no longer ships; delete them`,
    );
  }
});

test("vendored skills are byte-identical to examples/skills", async () => {
  for (const { skill, dirs } of SKILL_TARGETS) {
    const files = (await readdir(join(SKILLS_DIR, skill))).sort();
    assert.ok(files.includes("SKILL.md"), `${skill} must ship a SKILL.md`);

    for (const dir of dirs) {
      const target = join(dir, skill);
      assert.deepEqual(
        (await readdir(target)).sort(),
        files,
        `${relative(ROOT, target)} has a different file set; run node examples/memory-plugin-shared/sync.mjs`,
      );
      for (const file of files) {
        assert.equal(
          await readFile(join(target, file), "utf-8"),
          await readFile(join(SKILLS_DIR, skill, file), "utf-8"),
          `${relative(ROOT, join(target, file))} is out of sync; run node examples/memory-plugin-shared/sync.mjs`,
        );
      }
    }
  }
});

// Skill loaders reject a description longer than this, and the skill then
// silently fails to load. Guard every SKILL.md we ship, synced or not.
const MAX_DESCRIPTION_LENGTH = 1024;

function readDescription(source) {
  const frontmatter = /^---\n([\s\S]*?)\n---\n/.exec(source);
  if (!frontmatter) return null;
  const lines = frontmatter[1].split("\n");
  const start = lines.findIndex((line) => /^description:/.test(line));
  if (start === -1) return null;
  const head = lines[start].slice("description:".length).trim();
  if (head && head !== ">" && head !== "|" && head !== ">-" && head !== "|-") {
    return head.replace(/^["']|["']$/g, "");
  }
  const body = [];
  for (const line of lines.slice(start + 1)) {
    if (/^\S/.test(line)) break;
    body.push(line.trim());
  }
  return body.join(" ").trim();
}

async function findSkillFiles(dir) {
  const found = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) found.push(...(await findSkillFiles(full)));
    else if (entry.name === "SKILL.md") found.push(full);
  }
  return found;
}

test("shipped skill descriptions stay within the loader limit", async () => {
  const files = await findSkillFiles(join(ROOT, "examples"));
  assert.ok(files.length > 0, "expected at least one SKILL.md");

  for (const file of files) {
    const description = readDescription(await readFile(file, "utf-8"));
    assert.ok(description, `${relative(ROOT, file)} has no frontmatter description`);
    assert.ok(
      description.length <= MAX_DESCRIPTION_LENGTH,
      `${relative(ROOT, file)} description is ${description.length} chars, over the ${MAX_DESCRIPTION_LENGTH} limit`,
    );
  }
});
