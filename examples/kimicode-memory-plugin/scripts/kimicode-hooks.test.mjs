import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { kimicode } from "./kimicode-adapter.mjs";
import { evaluateKimicodeUriGuard } from "./uri-guard.mjs";

const PLUGIN_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("uri guard deny omits hookEventName (Kimi Code extra keys are unused)", () => {
  const output = evaluateKimicodeUriGuard({
    tool_name: "Read",
    tool_input: { file_path: "viking://~/memories/profile.md" },
  });
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
  assert.equal(output.hookSpecificOutput.hookEventName, undefined);
  assert.match(output.hookSpecificOutput.permissionDecisionReason, /viking:\/\//);
});

test("uri guard allows non-viking paths with empty output", () => {
  const output = evaluateKimicodeUriGuard({
    tool_name: "Read",
    tool_input: { file_path: "/tmp/readme.md" },
  });
  assert.deepEqual(output, {});
});

test("kimi.plugin.json declares SessionEnd and PreCompact unlike ZCode", () => {
  const manifest = JSON.parse(readFileSync(join(PLUGIN_ROOT, "kimi.plugin.json"), "utf8"));
  const events = manifest.hooks.map((hook) => hook.event);
  assert.ok(events.includes("SessionEnd"));
  assert.ok(events.includes("PreCompact"));
  assert.ok(events.includes("Interrupt"));
  assert.ok(events.includes("SessionStart"));
  for (const hook of manifest.hooks) {
    assert.ok(!("matcher" in hook) || hook.matcher !== "", "omit empty matcher");
  }
});

/* Legacy config-file installation is intentionally no longer tested. */
test("native plugin manifest owns hook and MCP declarations", () => {
  const manifest = JSON.parse(readFileSync(join(PLUGIN_ROOT, "kimi.plugin.json"), "utf8"));
  assert.ok(manifest.mcpServers.openviking);
  assert.equal(typeof manifest.mcpServers.openviking.command, "string");
  assert.ok(manifest.hooks.length > 0);
});

test("Kimi hook delegates lifecycle ordering to the shared runner", () => {
  const source = readFileSync(join(PLUGIN_ROOT, "scripts", "kimicode-hook.mjs"), "utf8");
  assert.match(source, /shared\/hook-runner\.mjs/);
});

test("Kimi Interrupt stays synchronous while capture events detach", () => {
  assert.equal(kimicode.detachEvents.has("interrupt"), false);
  assert.deepEqual([...kimicode.detachEvents].sort(), ["pre-compact", "session-end", "stop"]);
});
