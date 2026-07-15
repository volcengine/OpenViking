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
          tool_output: JSON.stringify(results),
        },
      ],
    },
  ]);
});
