import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { manageSkill } from "./owned-skill.mjs";

test("ownership permits managed upgrades and removes only an unchanged managed skill", (t) => {
  const root = mkdtempSync(join(tmpdir(), "owned-skill-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = join(root, "source");
  const destination = join(root, "skills", "rule-search");
  const state = join(root, "ownership", "rule-search.json");
  mkdirSync(source);
  writeFileSync(join(source, "SKILL.md"), "Version 1");
  assert.equal(manageSkill("install", source, destination, state), true);
  writeFileSync(join(source, "SKILL.md"), "Version 2");
  assert.equal(manageSkill("install", source, destination, state), true);
  assert.equal(readFileSync(join(destination, "SKILL.md"), "utf8"), "Version 2");
  assert.equal(manageSkill("uninstall", "", destination, state), true);
  assert.throws(() => readFileSync(join(destination, "SKILL.md")), { code: "ENOENT" });
});

test("new custom files and corrupt ownership prevent removal", (t) => {
  const root = mkdtempSync(join(tmpdir(), "owned-skill-edits-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = join(root, "source");
  const destination = join(root, "skills", "rule-search");
  const state = join(root, "ownership", "rule-search.json");
  mkdirSync(source);
  writeFileSync(join(source, "SKILL.md"), "Original");
  manageSkill("install", source, destination, state);
  writeFileSync(join(destination, "custom.txt"), "Keep this");
  assert.equal(manageSkill("uninstall", "", destination, state), false);
  assert.equal(readFileSync(join(destination, "custom.txt"), "utf8"), "Keep this");
  rmSync(join(destination, "custom.txt"));
  writeFileSync(state, "{broken");
  assert.equal(manageSkill("uninstall", "", destination, state), false);
  assert.equal(readFileSync(join(destination, "SKILL.md"), "utf8"), "Original");
});
