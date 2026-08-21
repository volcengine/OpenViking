import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { Config } from "@deepseek-ai/dsh-skill-filesystem";
import { apply } from "./index.mjs";
import { buildSkillsConfig, mountOpenVikingSkills, SKILLS_DIR } from "./skills.mjs";

test("the provider config validates against the pinned provider's own schema", () => {
  const parsed = Config(buildSkillsConfig());

  assert.equal(parsed.providerName, "openviking");
  // Isolated roots: DSH already mounts a `filesystem` provider over the project
  // and user skill dirs, and this one must not duplicate that catalog.
  assert.equal(parsed.includeDefaultRoots, false);
  assert.deepEqual(parsed.customSkillDirs, [SKILLS_DIR]);
});

test("the vendored skill ships at the served path", () => {
  const skill = readFileSync(join(SKILLS_DIR, "openviking-memory", "SKILL.md"), "utf8");

  assert.match(skill, /^---\nname: openviking-memory\n/);
  // The skill text must stay harness-neutral: dsh publishes the tools under an
  // `mcp__openviking__` prefix, which is one of the forms it already names.
  assert.match(skill, /mcp__openviking__/);
});

test("mounting passes the provider plugin and its config to the host context", () => {
  const mounted = [];
  mountOpenVikingSkills({ plugin: (plugin, config) => mounted.push({ plugin, config }) });

  assert.equal(mounted.length, 1);
  assert.equal(mounted[0].plugin.name, "skill-filesystem");
  assert.deepEqual(mounted[0].plugin.inject, ["skills"]);
  assert.equal(mounted[0].config.providerName, "openviking");
});

test("apply mounts both the tool surface and the skill", () => {
  const mounted = [];
  apply({
    logger: { debug() {} },
    provide() {},
    effect(execute) { execute(); return async () => {}; },
    plugin: (plugin, config) => mounted.push({ name: plugin.name, config }),
    tools: { register() {} },
    on() {},
  }, { endpoint: "http://127.0.0.1:1933", workspacePeer: false });

  assert.deepEqual(mounted.map(entry => entry.name).sort(), ["mcp-client", "skill-filesystem"]);
});
