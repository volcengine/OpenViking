import assert from "node:assert/strict";
import test from "node:test";

import { extractCaptureTranscript, extractCaptureTurns } from "./capture-utils.mjs";

const CAPTURE_CONFIG = {
  captureAssistantTurns: true,
  captureToolMaxChars: 1000000,
  captureMaxLength: 24000,
};

const startupBlocks = [
  "<recommended_plugins>\nHere is a list of plugins that are available but not installed.\n\n- Example (example@marketplace)\n</recommended_plugins>",
  "# AGENTS.md instructions\n\n<INSTRUCTIONS>\nUse Chinese for answers.\n</INSTRUCTIONS>",
  "<environment_context>\n  <cwd>/tmp/project</cwd>\n  <shell>zsh</shell>\n  <current_date>2026-09-25</current_date>\n  <timezone>Asia/Singapore</timezone>\n  <filesystem><permission_profile type=\"disabled\" /></filesystem>\n</environment_context>",
];

function userEntry(...texts) {
  return {
    type: "response_item",
    payload: { type: "message", role: "user", content: texts.map((text) => ({ type: "input_text", text })) },
  };
}

test("excludes complete Codex startup blocks and records their old turn positions", () => {
  const { turns, excludedLegacyTurnIndices } = extractCaptureTranscript([
    { type: "session_meta", payload: {} },
    userEntry(...startupBlocks),
    { type: "turn_context", payload: {} },
    userEntry("How does capture work?"),
  ], CAPTURE_CONFIG);
  assert.deepEqual(excludedLegacyTurnIndices, [0]);
  assert.deepEqual(turns.map((turn) => turn.text), ["How does capture work?"]);
});

test("records separate excluded startup messages at their old indices", () => {
  const { turns, excludedLegacyTurnIndices } = extractCaptureTranscript([
    userEntry(startupBlocks[0]),
    userEntry(startupBlocks[1], startupBlocks[2]),
    { type: "turn_context", payload: {} },
    userEntry("Real question"),
  ], CAPTURE_CONFIG);
  assert.deepEqual(excludedLegacyTurnIndices, [0, 1]);
  assert.deepEqual(turns.map((turn) => turn.text), ["Real question"]);
});

test("excludes the legacy path-qualified AGENTS.md startup block", () => {
  const legacyAgents = "# AGENTS.md instructions for /tmp/project\n\n<INSTRUCTIONS>\nUse Chinese for answers.\n</INSTRUCTIONS>";
  const { turns, excludedLegacyTurnIndices } = extractCaptureTranscript([
    userEntry(legacyAgents),
    { type: "turn_context", payload: {} },
    userEntry("Real question"),
  ], CAPTURE_CONFIG);
  assert.deepEqual(excludedLegacyTurnIndices, [0]);
  assert.deepEqual(turns.map((turn) => turn.text), ["Real question"]);
});

test("preserves user text mixed into a startup message", () => {
  const { turns, excludedLegacyTurnIndices } = extractCaptureTranscript([
    userEntry(`${startupBlocks[0]}\nPlease explain this plugin list.`, startupBlocks[1], startupBlocks[2]),
    { type: "turn_context", payload: {} },
    userEntry("Why does the rollout contain AGENTS.md instructions and <environment_context>?"),
  ], CAPTURE_CONFIG);
  assert.deepEqual(excludedLegacyTurnIndices, []);
  assert.deepEqual(turns.map((turn) => turn.text), [
    "Please explain this plugin list.",
    "Why does the rollout contain AGENTS.md instructions and <environment_context>?",
  ]);
});

test("keeps incomplete wrappers and normal-turn quotations", () => {
  const quoted = `${startupBlocks[0]}\n\n${startupBlocks[1]}`;
  const { turns, excludedLegacyTurnIndices } = extractCaptureTranscript([
    userEntry("<recommended_plugins>\nmissing closing tag"),
    { type: "turn_context", payload: {} },
    userEntry(quoted),
  ], CAPTURE_CONFIG);
  assert.deepEqual(excludedLegacyTurnIndices, []);
  assert.equal(turns.length, 2);
  assert.match(turns[1].text, /AGENTS\.md instructions/);
});

function toolParts(turns, toolName) {
  return turns
    .flatMap((turn) => turn.parts)
    .filter((part) => part.type === "tool" && part.tool_name === toolName);
}

test("keeps timestamped user log lines on the capture wire", () => {
  const log = "2024-01-01 12:00:00 ERROR something broke\n2024-01-01 12:00:01 INFO retry";
  const turns = extractCaptureTurns([{
    type: "response_item",
    payload: { type: "message", role: "user", content: [{ type: "input_text", text: log }] },
  }], CAPTURE_CONFIG);
  assert.equal(turns.length, 1);
  assert.deepEqual(turns[0].parts, [{ type: "text", text: log }]);
});

test("pairs current Codex function_call records by call_id", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "response_item",
        payload: {
          type: "function_call",
          id: "fc-item-1",
          call_id: "logical-call-1",
          name: "exec_command",
          arguments: JSON.stringify({ cmd: "pwd" }),
        },
      },
      {
        type: "response_item",
        payload: {
          type: "function_call_output",
          id: "fco-item-1",
          call_id: "logical-call-1",
          output: "project root",
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(
    turns.flatMap((turn) => turn.parts),
    [
      {
        type: "tool",
        tool_id: "logical-call-1",
        tool_name: "exec_command",
        tool_status: "running",
        tool_input: { cmd: "pwd" },
      },
      {
        type: "tool",
        tool_id: "logical-call-1",
        tool_name: "exec_command",
        tool_status: "completed",
        tool_output: "project root",
      },
    ],
  );
});

test("captures current Codex custom_tool_call records", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "response_item",
        payload: {
          type: "custom_tool_call",
          id: "ctc-item-1",
          call_id: "custom-call-1",
          status: "completed",
          name: "exec",
          input: "sed -n '1,200p' /tmp/skills/tdd/SKILL.md",
        },
      },
      {
        type: "response_item",
        payload: {
          type: "custom_tool_call_output",
          id: "ctco-item-1",
          call_id: "custom-call-1",
          output: "skill contents",
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(
    turns.flatMap((turn) => turn.parts),
    [
      {
        type: "tool",
        tool_id: "custom-call-1",
        tool_name: "exec",
        tool_status: "running",
        tool_input: { value: "sed -n '1,200p' /tmp/skills/tdd/SKILL.md" },
      },
      {
        type: "tool",
        tool_id: "custom-call-1",
        tool_name: "exec",
        tool_status: "completed",
        tool_output: "skill contents",
      },
    ],
  );
});

test("captures current Codex tool_search records", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "response_item",
        payload: {
          type: "tool_search_call",
          id: "ts-item-1",
          call_id: "tool-search-1",
          status: "completed",
          arguments: { query: "calendar tools", limit: 3 },
        },
      },
      {
        type: "response_item",
        payload: {
          type: "tool_search_output",
          id: "tso-item-1",
          call_id: "tool-search-1",
          status: "completed",
          tools: [{ name: "calendar.search" }],
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(
    turns.flatMap((turn) => turn.parts),
    [
      {
        type: "tool",
        tool_id: "tool-search-1",
        tool_name: "tool_search",
        tool_status: "running",
        tool_input: { query: "calendar tools", limit: 3 },
      },
      {
        type: "tool",
        tool_id: "tool-search-1",
        tool_name: "tool_search",
        tool_status: "completed",
        tool_output: JSON.stringify({ tools: [{ name: "calendar.search" }] }),
      },
    ],
  );
});

test("deduplicates function records also reported as mcp_tool_call_end", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "response_item",
        payload: {
          type: "function_call",
          id: "fc-mcp-1",
          call_id: "mcp-call-1",
          name: "js",
          arguments: JSON.stringify({ code: "return 1" }),
        },
      },
      {
        type: "response_item",
        payload: {
          type: "function_call_output",
          id: "fco-mcp-1",
          call_id: "mcp-call-1",
          output: "1",
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "mcp_tool_call_end",
          call_id: "mcp-call-1",
          invocation: {
            server: "node-repl",
            tool: "js",
            arguments: { code: "return 1" },
          },
          result: {
            Ok: {
              content: [{ type: "text", text: "1" }],
            },
          },
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  const parts = turns.flatMap((turn) => turn.parts);
  assert.equal(parts.length, 2);
  assert.deepEqual(parts.map((part) => part.tool_status), ["running", "completed"]);
  assert.deepEqual(parts.map((part) => part.tool_id), ["mcp-call-1", "mcp-call-1"]);
});

test("keeps outer exec and captures its nested MCP tool by the nested call id", () => {
  const uri = "viking://user/test/memories/experiences/refund.md";
  const turns = extractCaptureTurns(
    [
      {
        type: "response_item",
        payload: {
          type: "custom_tool_call",
          call_id: "call-outer",
          name: "exec",
          input: "await tools.mcp__openviking_memory__read(...)",
        },
      },
      {
        type: "response_item",
        payload: {
          type: "custom_tool_call_output",
          call_id: "call-outer",
          output: "nested call completed",
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "mcp_tool_call_end",
          call_id: "code-mode-nested:7:call-outer:exec-read",
          invocation: {
            server: "openviking-memory",
            tool: "read",
            arguments: { uris: [uri] },
          },
          result: {
            Ok: {
              content: [{ type: "text", text: JSON.stringify({ uri, content: "refund policy" }) }],
            },
          },
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(toolParts(turns, "exec").map((part) => part.tool_status), [
    "running",
    "completed",
  ]);
  assert.deepEqual(toolParts(turns, "read"), [
    {
      type: "tool",
      tool_id: "code-mode-nested:7:call-outer:exec-read",
      tool_name: "read",
      tool_status: "running",
      tool_input: { uris: [uri] },
    },
    {
      type: "tool",
      tool_id: "code-mode-nested:7:call-outer:exec-read",
      tool_name: "read",
      tool_status: "completed",
      tool_output: JSON.stringify({ uri, content: "refund policy" }),
    },
  ]);
});

test("keeps outer exec when a nested tool reuses its call id", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "response_item",
        payload: {
          type: "custom_tool_call",
          call_id: "shared-call-id",
          name: "exec",
          input: "await tools.mcp__openviking_memory__find(...)",
        },
      },
      {
        type: "response_item",
        payload: {
          type: "custom_tool_call_output",
          call_id: "shared-call-id",
          output: "nested call completed",
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "mcp_tool_call_end",
          call_id: "shared-call-id",
          invocation: {
            server: "openviking-memory",
            tool: "find",
            arguments: { query: "refund" },
          },
          result: { Ok: { content: [{ type: "text", text: "found" }] } },
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(toolParts(turns, "exec").map((part) => part.tool_status), [
    "running",
    "completed",
  ]);
  assert.deepEqual(toolParts(turns, "find").map((part) => part.tool_status), [
    "running",
    "completed",
  ]);
});

test("captures legacy command and patch completion events", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "event_msg",
        payload: {
          type: "exec_command_end",
          call_id: "exec-shell",
          command: ["pwd"],
          cwd: "/workspace",
          aggregated_output: "/workspace\n",
          exit_code: 0,
          status: "completed",
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "patch_apply_end",
          call_id: "exec-patch",
          changes: { "README.md": { type: "update" } },
          stdout: "Done!",
          stderr: "",
          success: true,
          status: "completed",
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(toolParts(turns, "exec_command"), [
    {
      type: "tool",
      tool_id: "exec-shell",
      tool_name: "exec_command",
      tool_status: "running",
      tool_input: { command: ["pwd"], cwd: "/workspace" },
    },
    {
      type: "tool",
      tool_id: "exec-shell",
      tool_name: "exec_command",
      tool_status: "completed",
      tool_output: "/workspace",
    },
  ]);
  assert.deepEqual(toolParts(turns, "apply_patch"), [
    {
      type: "tool",
      tool_id: "exec-patch",
      tool_name: "apply_patch",
      tool_status: "running",
      tool_input: { changes: { "README.md": { type: "update" } } },
    },
    {
      type: "tool",
      tool_id: "exec-patch",
      tool_name: "apply_patch",
      tool_status: "completed",
      tool_output: "Done!",
    },
  ]);
});

test("falls back to command stdout and stderr when aggregated output is absent", () => {
  const turns = extractCaptureTurns(
    [{
      type: "event_msg",
      payload: {
        type: "exec_command_end",
        call_id: "exec-shell-streams",
        command: ["check"],
        stdout: "partial output",
        stderr: "failure detail",
        exit_code: 2,
        status: "failed",
      },
    }],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(toolParts(turns, "exec_command").at(-1), {
    type: "tool",
    tool_id: "exec-shell-streams",
    tool_name: "exec_command",
    tool_status: "error",
    tool_output: "partial output\nfailure detail",
  });
});

test("uses the final legacy command lifecycle event", async (t) => {
  for (const exitCode of [1, 130]) {
    await t.test(`records exit ${exitCode} as an error`, () => {
      const callId = `exec-backgrounded-${exitCode}`;
      const turns = extractCaptureTurns(
        [
          {
            type: "event_msg",
            payload: {
              type: "exec_command_end",
              call_id: callId,
              command: ["long-running-command"],
              aggregated_output: "",
              exit_code: 0,
              status: "backgrounded",
            },
          },
          {
            type: "event_msg",
            payload: {
              type: "exec_command_end",
              call_id: callId,
              command: ["long-running-command"],
              aggregated_output: `failed with exit ${exitCode}`,
              exit_code: exitCode,
              status: "failed",
            },
          },
        ],
        CAPTURE_CONFIG,
      );

      assert.deepEqual(toolParts(turns, "exec_command").map((part) => part.tool_status), [
        "running",
        "error",
      ]);
      assert.equal(
        toolParts(turns, "exec_command").at(-1).tool_output,
        `failed with exit ${exitCode}`,
      );
    });
  }
});

test("captures completed paginated tools without duplicating legacy events", () => {
  const experienceUri = "viking://user/test/memories/experiences/refund.md";
  const turns = extractCaptureTurns(
    [
      {
        type: "event_msg",
        payload: {
          type: "item_completed",
          item: {
            type: "McpToolCall",
            id: "nested-find",
            server: "openviking-memory",
            tool: "find",
            arguments: { query: "refund" },
            status: "completed",
            result: { content: [{ type: "text", text: "found" }], isError: false },
          },
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "mcp_tool_call_end",
          call_id: "nested-find",
          invocation: {
            server: "openviking-memory",
            tool: "find",
            arguments: { query: "refund" },
          },
          result: { Ok: { content: [{ type: "text", text: "found" }] } },
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "item_completed",
          item: {
            type: "McpToolCall",
            id: "nested-read",
            server: "openviking-memory",
            tool: "read",
            arguments: { uris: [experienceUri] },
            status: "completed",
            result: {
              content: [{
                type: "text",
                text: JSON.stringify({ uri: experienceUri, content: "refund policy" }),
              }],
              isError: false,
            },
          },
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "item_completed",
          item: {
            type: "CommandExecution",
            id: "nested-command",
            command: ["false"],
            cwd: "/workspace",
            parsed_cmd: [],
            source: "unified_exec_startup",
            status: "failed",
            aggregated_output: "command failed",
            exit_code: 1,
          },
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "exec_command_end",
          call_id: "nested-command",
          command: ["false"],
          cwd: "/workspace",
          status: "backgrounded",
          aggregated_output: "backgrounded",
          exit_code: 0,
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "item_completed",
          item: {
            type: "FileChange",
            id: "nested-patch",
            changes: { "src/a.js": { type: "add" } },
            status: "completed",
            stdout: "Done!",
            stderr: "",
          },
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "item_completed",
          item: {
            type: "DynamicToolCall",
            id: "dynamic-1",
            tool: "lookup",
            arguments: { id: 7 },
            status: "completed",
            success: true,
            content_items: [{ type: "inputText", text: "record 7" }],
          },
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(toolParts(turns, "find").map((part) => part.tool_status), [
    "running",
    "completed",
  ]);
  assert.deepEqual(toolParts(turns, "read"), [
    {
      type: "tool",
      tool_id: "nested-read",
      tool_name: "read",
      tool_status: "running",
      tool_input: { uris: [experienceUri] },
    },
    {
      type: "tool",
      tool_id: "nested-read",
      tool_name: "read",
      tool_status: "completed",
      tool_output: JSON.stringify({ uri: experienceUri, content: "refund policy" }),
    },
  ]);
  assert.deepEqual(toolParts(turns, "exec_command").map((part) => part.tool_status), [
    "running",
    "error",
  ]);
  assert.equal(toolParts(turns, "exec_command").at(-1).tool_output, "command failed");
  assert.deepEqual(toolParts(turns, "apply_patch").map((part) => part.tool_status), [
    "running",
    "completed",
  ]);
  assert.deepEqual(toolParts(turns, "lookup"), [
    {
      type: "tool",
      tool_id: "dynamic-1",
      tool_name: "lookup",
      tool_status: "running",
      tool_input: { id: 7 },
    },
    {
      type: "tool",
      tool_id: "dynamic-1",
      tool_name: "lookup",
      tool_status: "completed",
      tool_output: JSON.stringify([{ type: "inputText", text: "record 7" }]),
    },
  ]);
});

test("expands history mutation items and deduplicates matching response items", () => {
  const call = {
    type: "function_call",
    id: "fc-history-1",
    call_id: "history-call-1",
    name: "exec",
    arguments: "await Promise.resolve()",
  };
  const output = {
    type: "function_call_output",
    id: "fco-history-1",
    call_id: "history-call-1",
    output: "done",
  };
  const turns = extractCaptureTurns(
    [
      {
        type: "history_mutation",
        payload: {
          operation: "append",
          items: [
            {
              type: "message",
              id: "history-message-1",
              role: "assistant",
              content: [{ type: "output_text", text: "running a check" }],
            },
            call,
            output,
          ],
        },
      },
      { type: "response_item", payload: call },
      { type: "response_item", payload: output },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(toolParts(turns, "exec").map((part) => part.tool_status), [
    "running",
    "completed",
  ]);
  assert.equal(turns.some((turn) => turn.text === "running a check"), true);
});

test("deduplicates custom tool calls repeated as history function calls", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "response_item",
        payload: {
          type: "custom_tool_call",
          id: "custom-item-1",
          call_id: "outer-call-1",
          name: "exec",
          input: "await Promise.resolve()",
        },
      },
      {
        type: "history_mutation",
        payload: {
          operation: "append",
          items: [{
            type: "function_call",
            id: "function-item-1",
            call_id: "outer-call-1",
            name: "exec",
            arguments: "await Promise.resolve()",
          }],
        },
      },
      {
        type: "history_mutation",
        payload: {
          operation: "append",
          items: [{
            type: "function_call_output",
            id: "function-output-1",
            call_id: "outer-call-1",
            output: "done",
          }],
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(toolParts(turns, "exec").map((part) => part.tool_status), [
    "running",
    "completed",
  ]);
});

test("captures Codex subagent messages and activity with identity", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "event_msg",
        payload: {
          type: "agent_message",
          message: "UI duplicate of a normal assistant response",
        },
      },
      {
        type: "response_item",
        payload: {
          type: "agent_message",
          author: "reviewer",
          recipient: "main",
          content: [{ type: "input_text", text: "Found a pairing bug." }],
        },
      },
      {
        type: "event_msg",
        payload: {
          type: "sub_agent_activity",
          event_id: "subagent-event-1",
          occurred_at_ms: 1234,
          agent_thread_id: "thread-1",
          agent_path: "reviewer",
          kind: "started",
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(turns[0], {
    role: "assistant",
    text: "[agent-message reviewer -> main]\nFound a pairing bug.",
    parts: [
      {
        type: "text",
        text: "[agent-message reviewer -> main]\nFound a pairing bug.",
      },
    ],
  });
  assert.deepEqual(turns[1].parts, [
    {
      type: "tool",
      tool_id: "subagent-activity:subagent-event-1",
      tool_name: "sub_agent_activity",
      tool_status: "completed",
      tool_output: JSON.stringify({
        agent_thread_id: "thread-1",
        agent_path: "reviewer",
        kind: "started",
        occurred_at_ms: 1234,
      }),
    },
  ]);
});

test("captures generic OpenViking MCP calls as standard tool parts", () => {
  const results = {
    results: [
      {
        uri: "viking://user/test/memories/experiences/无订单号换货处理.md",
        title: "无订单号换货处理",
      },
    ],
  };
  const turns = extractCaptureTurns(
    [
      {
        type: "event_msg",
        payload: {
          type: "mcp_tool_call_end",
          call_id: "exec-search-1",
          invocation: {
            server: "openviking-memory",
            tool: "find",
            arguments: {
              query: "无订单号换货",
              target_uri: "viking://user/test/memories/experiences/",
            },
          },
          result: {
            Ok: {
              content: [{ type: "text", text: JSON.stringify(results) }],
            },
          },
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(turns, [
    {
      role: "assistant",
      text: "[tool-call find] {\"query\":\"无订单号换货\",\"target_uri\":\"viking://user/test/memories/experiences/\"}",
      parts: [
        {
          type: "tool",
          tool_id: "exec-search-1",
          tool_name: "find",
          tool_status: "running",
          tool_input: {
            query: "无订单号换货",
            target_uri: "viking://user/test/memories/experiences/",
          },
        },
      ],
    },
    {
      role: "user",
      text: `[tool-result] ${JSON.stringify(results)}`,
      parts: [
        {
          type: "tool",
          tool_id: "exec-search-1",
          tool_name: "find",
          tool_status: "completed",
          tool_output: JSON.stringify(results),
        },
      ],
    },
  ]);
});

test("keeps MCP tool-level errors out of completed generic read tool parts", () => {
  const uri = "viking://user/test/memories/experiences/a.md";
  const turns = extractCaptureTurns(
    [
      {
        type: "event_msg",
        payload: {
          type: "mcp_tool_call_end",
          call_id: "exec-read-error",
          invocation: {
            server: "openviking-memory",
            tool: "read",
            arguments: { uri },
          },
          result: {
            Ok: {
              isError: true,
              content: [{ type: "text", text: "OpenViking request failed (HTTP 500)" }],
            },
          },
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  const parts = turns.flatMap((turn) => turn.parts);
  assert.deepEqual(parts.map((part) => part.tool_status), ["running", "error"]);
  assert.equal(parts.some((part) => part.tool_status === "completed"), false);
});

test("marks paginated MCP errors as failed tool parts", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "event_msg",
        payload: {
          type: "item_completed",
          item: {
            type: "McpToolCall",
            id: "nested-read-error",
            server: "openviking-memory",
            tool: "read",
            arguments: { uris: ["viking://user/test/memories/experiences/a.md"] },
            status: "failed",
            error: { message: "OpenViking request failed (HTTP 500)" },
          },
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  assert.deepEqual(toolParts(turns, "read").map((part) => part.tool_status), [
    "running",
    "error",
  ]);
  assert.match(toolParts(turns, "read")[1].tool_output, /HTTP 500/);
});

test("preserves generic read input when Codex truncates a long MCP result", () => {
  const uri = "viking://user/test/memories/experiences/long-experience.md";
  const turns = extractCaptureTurns(
    [
      {
        type: "event_msg",
        payload: {
          type: "mcp_tool_call_end",
          call_id: "exec-read-long",
          invocation: {
            server: "openviking-memory",
            tool: "read",
            arguments: { uri },
          },
          result: {
            Ok: {
              content: [
                {
                  type: "text",
                  text: JSON.stringify({ uri, content: "x".repeat(3000) }),
                },
              ],
            },
          },
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  const running = turns
    .flatMap((turn) => turn.parts)
    .find((part) => part.tool_status === "running");
  assert.deepEqual(running.tool_input, { uri });
});

test("keeps search Experience results parseable when snippets are long", () => {
  const uri = "viking://user/test/memories/experiences/long-search-result.md";
  const turns = extractCaptureTurns(
    [
      {
        type: "event_msg",
        payload: {
          type: "mcp_tool_call_end",
          call_id: "exec-search-long",
          invocation: {
            server: "openviking-memory",
            tool: "find",
            arguments: { query: "换货" },
          },
          result: {
            Ok: {
              content: [
                {
                  type: "text",
                  text: JSON.stringify({
                    results: [{ uri, title: "换货经验", snippet: "x".repeat(3000) }],
                  }),
                },
              ],
            },
          },
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  const completed = turns
    .flatMap((turn) => turn.parts)
    .find((part) => part.tool_status === "completed");
  assert.deepEqual(JSON.parse(completed.tool_output), {
    results: [{ uri, title: "换货经验", snippet: "x".repeat(3000) }],
  });
});

test("reports tool output verbatim so the server can externalize it", () => {
  const output = "x".repeat(50_000);
  const turns = extractCaptureTurns(
    [
      {
        type: "response_item",
        payload: {
          type: "function_call",
          call_id: "call-large-output",
          name: "shell",
          arguments: { command: "cat big.txt" },
        },
      },
      {
        type: "response_item",
        payload: {
          type: "function_call_output",
          call_id: "call-large-output",
          output,
        },
      },
    ],
    CAPTURE_CONFIG,
  );

  const completed = turns
    .flatMap((turn) => turn.parts)
    .find((part) => part.tool_status === "completed");
  assert.equal(completed.tool_output, output);
});

test("captureToolMaxChars still caps tool output when an operator lowers it", () => {
  const turns = extractCaptureTurns(
    [
      {
        type: "response_item",
        payload: {
          type: "function_call",
          call_id: "call-capped-output",
          name: "shell",
          arguments: { command: "cat big.txt" },
        },
      },
      {
        type: "response_item",
        payload: {
          type: "function_call_output",
          call_id: "call-capped-output",
          output: "y".repeat(5000),
        },
      },
    ],
    { ...CAPTURE_CONFIG, captureToolMaxChars: 1000 },
  );

  const completed = turns
    .flatMap((turn) => turn.parts)
    .find((part) => part.tool_status === "completed");
  assert.ok(completed.tool_output.length <= 1000);
  assert.match(completed.tool_output, /\[truncated\]$/);
});
