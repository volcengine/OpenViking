import assert from "node:assert/strict";
import test from "node:test";

import { extractCaptureTurns } from "./capture-utils.mjs";

test("captures Codex mcp_tool_call_end as standard tool parts", () => {
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
            tool: "search_experience",
            arguments: { query: "无订单号换货" },
          },
          result: {
            Ok: {
              content: [{ type: "text", text: JSON.stringify(results) }],
            },
          },
        },
      },
    ],
    {
      captureAssistantTurns: true,
      captureToolMaxChars: 2000,
      captureMaxLength: 24000,
    },
  );

  assert.deepEqual(turns, [
    {
      role: "assistant",
      text: "[tool-call search_experience] {\"query\":\"无订单号换货\"}",
      parts: [
        {
          type: "tool",
          tool_id: "exec-search-1",
          tool_name: "search_experience",
          tool_status: "running",
          tool_input: { query: "无订单号换货" },
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
          tool_name: "search_experience",
          tool_status: "completed",
          tool_input: { query: "无订单号换货" },
          tool_output: JSON.stringify(results),
        },
      ],
    },
  ]);
});

test("keeps MCP tool-level errors out of completed Experience tool parts", () => {
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
            tool: "read_experience",
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
    {
      captureAssistantTurns: true,
      captureToolMaxChars: 2000,
      captureMaxLength: 24000,
    },
  );

  const parts = turns.flatMap((turn) => turn.parts);
  assert.deepEqual(parts.map((part) => part.tool_status), ["running", "error"]);
  assert.equal(parts.some((part) => part.tool_status === "completed"), false);
});

test("preserves Experience usage metadata when Codex truncates a long MCP result", () => {
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
            tool: "read_experience",
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
    {
      captureAssistantTurns: true,
      captureToolMaxChars: 2000,
      captureMaxLength: 24000,
    },
  );

  const completed = turns
    .flatMap((turn) => turn.parts)
    .find((part) => part.tool_status === "completed");
  assert.deepEqual(completed.tool_input, { uri });
});

test("keeps search Experience URIs parseable when snippets exceed the capture limit", () => {
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
            tool: "search_experience",
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
    {
      captureAssistantTurns: true,
      captureToolMaxChars: 2000,
      captureMaxLength: 24000,
    },
  );

  const completed = turns
    .flatMap((turn) => turn.parts)
    .find((part) => part.tool_status === "completed");
  assert.deepEqual(JSON.parse(completed.tool_output), { results: [{ uri }] });
});
