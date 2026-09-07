import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";
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

test("merge-config is idempotent and preserves unrelated hooks", () => {
  const dir = mkdtempSync(join(tmpdir(), "kc-merge-"));
  const toml = join(dir, "config.toml");
  const mcp = join(dir, "mcp.json");
  writeFileSync(
    toml,
    'default_model = "k3"\n\n[[hooks]]\nevent = "Stop"\ncommand = "echo herdr"\ntimeout = 10\n',
  );
  writeFileSync(mcp, '{"mcpServers":{"other":{"command":"true"}}}\n');
  const script = join(PLUGIN_ROOT, "scripts", "merge-config.mjs");
  const run = () =>
    spawnSync(process.execPath, [script, toml, mcp, PLUGIN_ROOT, process.execPath], {
      encoding: "utf8",
    });
  assert.equal(run().status, 0);
  assert.equal(run().status, 0);
  const text = readFileSync(toml, "utf8");
  assert.equal(text.split(">>> openviking kimicode integration").length - 1, 1);
  assert.match(text, /command = "echo herdr"/);
  assert.match(text, /event = "SessionEnd"/);
  assert.match(text, /matcher = "Read\|Glob\|Grep"/);
  const mcpJson = JSON.parse(readFileSync(mcp, "utf8"));
  assert.equal(mcpJson.mcpServers.other.command, "true");
  assert.equal(mcpJson.mcpServers.openviking.env.OPENVIKING_INTEGRATION_ID, "kimicode");
});
