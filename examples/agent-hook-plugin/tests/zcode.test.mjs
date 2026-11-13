import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { evaluateHostUriGuard } from "../scripts/uri-guard.mjs";
import {
  expectExit,
  readRequestBody,
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeCredentialFiles,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

const PLUGIN_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const RECALL_DOCUMENTS = [
  { uri: "viking://agent/skills/noisy/SKILL.md", category: "skills", text: "EXCLUDED_SKILL_DOCUMENT" },
  { uri: "viking://user/resources/project/README.md", category: "resources", text: "NORMAL_PROJECT_DOCUMENT" },
];

for (const scenario of [
  { name: "no recall exclusion configured", excludeUris: undefined },
  { name: "an empty recall exclusion list", excludeUris: [] },
  { name: "an exact skill URI excluded", excludeUris: [RECALL_DOCUMENTS[0].uri] },
  { name: "the exclusion retargeted to the ordinary document", excludeUris: [RECALL_DOCUMENTS[1].uri] },
]) {
  test(`ZCode prompt recall injects the configured documents with ${scenario.name}`, async () => {
    const restoreEnv = scrubOpenVikingEnv();
    const requestBodies = [];
    try {
      await withMockOpenViking(async (req, res) => {
        assert.equal(req.method, "POST");
        assert.equal(req.url, "/api/v1/search/search");
        const body = await readRequestBody(req);
        requestBodies.push(body);
        assert.equal(body.mode, "context");
        assert.equal(body.query, "What project setting should I use?");
        assert.equal(body.session_id, "zc-recall-exclusion-session");
        assert.equal(body.peer_scope, "actor");
        // This fixture implements only exact URI membership, not subtree filtering.
        const entries = RECALL_DOCUMENTS.filter((entry) => !body.exclude_uris?.includes(entry.uri));
        writeJson(res, {
          status: "ok",
          result: { rendered: entries.map((entry) => entry.text).join("\n"), entries, stats: { used_tokens: 20 } },
        });
      }, async (baseUrl, requests) => {
        const settings = {
          recallPeerScope: "actor",
          recallCompress: "off",
          recallQueryExpansion: "off",
          recallDedupTurns: 0,
        };
        if (scenario.excludeUris !== undefined) settings.recallExcludeUris = scenario.excludeUris;
        const files = await writeCredentialFiles("ov-zcode-recall-exclude-", {
          ovcli: { url: baseUrl, api_key: "", plugin: { zcode: settings } },
          ov: {},
        });
        try {
          const result = expectExit(await runHookScript(join(PLUGIN_ROOT, "scripts", "hook.mjs"), {
            argv: ["user-prompt-submit", "zcode"],
            input: {
              prompt: "What project setting should I use?",
              sessionId: "recall-exclusion-session",
              request_id: "recall-exclusion-prompt",
              cwd: files.dir,
            },
            cwd: files.dir,
            env: {
              ...files.env,
              OPENVIKING_PEER_ID: "fixture-peer",
              OPENVIKING_HOOK_STATE_DIR: join(files.dir, "hook-state"),
              OPENVIKING_STATE_DIR: join(files.dir, "state"),
            },
          }));
          assert.equal(requests.length, 1, "a successful context response must not use retrieval fallbacks");
          const output = JSON.parse(result.stdout);
          assert.equal(output.hookSpecificOutput.hookEventName, "UserPromptSubmit");
          const block = output.hookSpecificOutput.additionalContext;
          for (const entry of RECALL_DOCUMENTS) {
            assert.equal(
              block.includes(entry.text),
              !scenario.excludeUris?.includes(entry.uri),
              `injected context should respect the exact exclusion for ${entry.uri}`,
            );
          }
          assert.deepEqual(
            requestBodies[0].exclude_uris,
            scenario.excludeUris?.length ? scenario.excludeUris : undefined,
            "the configured exact URI must reach the context-mode HTTP request",
          );
        } finally {
          await rm(files.dir, { recursive: true, force: true });
        }
      });
    } finally {
      restoreEnv();
    }
  });
}

// ---------------------------------------------------------------------------
// URI Guard output schema (the #1 silent-failure mode in the adversarial review)
// ZCode's strict JSON schema rejects any unrecognized key. These tests assert
// the output contains ONLY ZCode-recognized keys.
// ---------------------------------------------------------------------------

test("uri guard deny output contains only recognized keys", () => {
  const output = evaluateHostUriGuard("zcode", {
    tool_name: "Read",
    tool_input: { file_path: "viking://~/memories/profile.md" },
  });
  assert.ok(output.hookSpecificOutput);
  assert.equal(output.hookSpecificOutput.hookEventName, "PreToolUse");
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
  assert.ok(output.hookSpecificOutput.permissionDecisionReason);
  // Must NOT contain Claude-Code-isms
  assert.equal(output.decision, undefined);
  assert.equal(output.hookSpecificOutput.decision, undefined);
});

test("uri guard deny reason includes MCP redirect", () => {
  const output = evaluateHostUriGuard("zcode", {
    tool_name: "Read",
    tool_input: { file_path: "viking://resources/myproject/docs.md" },
  });
  const reason = output.hookSpecificOutput?.permissionDecisionReason || "";
  assert.ok(reason.includes("MCP"), "reason should mention MCP");
});

test("uri guard pass-through for non-viking URIs returns empty", () => {
  const output = evaluateHostUriGuard("zcode", {
    tool_name: "Read",
    tool_input: { file_path: "/tmp/local-file.txt" },
  });
  assert.equal(Object.keys(output).length, 0);
});

test("uri guard pass-through for Glob with local pattern", () => {
  const output = evaluateHostUriGuard("zcode", {
    tool_name: "Glob",
    tool_input: { pattern: "**/*.ts" },
  });
  assert.equal(Object.keys(output).length, 0);
});

test("uri guard deny for Glob with viking URI", () => {
  const output = evaluateHostUriGuard("zcode", {
    tool_name: "Glob",
    tool_input: { pattern: "viking://user/memories/**" },
  });
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
});

test("uri guard pass-through for Grep searching local files for a viking URI", () => {
  const output = evaluateHostUriGuard("zcode", {
    tool_name: "Grep",
    tool_input: { pattern: "viking://user/", path: "/repo" },
  });
  assert.deepEqual(output, {});
});

test("uri guard handles alternative tool_input field names", () => {
  const output = evaluateHostUriGuard("zcode", {
    tool_name: "Read",
    toolInput: { file_path: "viking://user/test.md" },
  });
  assert.equal(output.hookSpecificOutput?.permissionDecision, "deny");
});

test("uri guard handles alternative tool_name field names", () => {
  const output = evaluateHostUriGuard("zcode", {
    toolName: "Read",
    tool_input: { file_path: "viking://user/test.md" },
  });
  assert.equal(output.hookSpecificOutput?.permissionDecision, "deny");
});

test("uri guard notice for a shell command carries only hookEventName and additionalContext", () => {
  const output = evaluateHostUriGuard("zcode", {
    tool_name: "Bash",
    tool_input: { command: "cat viking://user/test.md" },
  });
  assert.deepEqual(Object.keys(output), ["hookSpecificOutput"]);
  assert.deepEqual(Object.keys(output.hookSpecificOutput).sort(), ["additionalContext", "hookEventName"]);
  assert.equal(output.hookSpecificOutput.hookEventName, "PreToolUse");
  assert.match(output.hookSpecificOutput.additionalContext, /viking:\/\/user\/test\.md/);
  assert.match(output.hookSpecificOutput.additionalContext, /ignore this notice/);
});

test("PreToolUse matcher names no shell tool", () => {
  const hooks = JSON.parse(readFileSync(join(PLUGIN_ROOT, "hosts", "zcode", "hooks.json"), "utf8"));
  assert.deepEqual(
    hooks.hooks.PreToolUse.map((entry) => entry.matcher),
    ["Read|Glob|Grep"],
    "a shell tool here sends ZCode a PreToolUse additionalContext its strict schema is not verified to accept",
  );
});

// ---------------------------------------------------------------------------
// Source-file conventions: the installer expands the plugin root, and it knows
// one spelling of it. A host-specific spelling reaches ZCode verbatim, where
// nothing expands it.
// ---------------------------------------------------------------------------

const HOST_ROOT_VARIABLE = /(?:CLAUDE|CURSOR|ZCODE)_PLUGIN_ROOT/u;

test("hooks.json templates the plugin root the installer expands", () => {
  const hooksJson = readFileSync(join(PLUGIN_ROOT, "hosts", "zcode", "hooks.json"), "utf8");
  assert.ok(
    !HOST_ROOT_VARIABLE.test(hooksJson),
    "hooks.json should use __OPENVIKING_PLUGIN_ROOT__, not a host-specific root variable",
  );
  assert.ok(
    hooksJson.includes("__OPENVIKING_PLUGIN_ROOT__"),
    "hooks.json should reference __OPENVIKING_PLUGIN_ROOT__",
  );
});

test(".mcp.json templates the plugin root the installer expands", () => {
  const mcpJson = readFileSync(join(PLUGIN_ROOT, "hosts", "zcode", ".mcp.json"), "utf8");
  assert.ok(
    !HOST_ROOT_VARIABLE.test(mcpJson),
    ".mcp.json should use __OPENVIKING_PLUGIN_ROOT__, not a host-specific root variable",
  );
  assert.ok(
    mcpJson.includes("__OPENVIKING_PLUGIN_ROOT__"),
    ".mcp.json should reference __OPENVIKING_PLUGIN_ROOT__",
  );
});
