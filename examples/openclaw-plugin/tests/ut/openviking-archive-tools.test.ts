import { describe, expect, it, vi } from "vitest";

import {
  registerOpenVikingArchiveTools,
  type OpenVikingArchiveToolsDeps,
} from "../../plugin/openviking-archive-tools.js";

type Tool = {
  description: string;
  execute: (toolCallId: string, params: Record<string, unknown>) => Promise<{
    content: Array<{ type: string; text: string }>;
    details: Record<string, unknown>;
  }>;
};

function makeArchiveSearch(matches: Array<{ uri: string; line: number; content: string }>) {
  let archiveSearch: Tool | undefined;
  const traceRecorder = { recordAndFlush: vi.fn() };
  const deps: OpenVikingArchiveToolsDeps = {
    registerTool: (factory) => {
      const tool = (factory as (ctx: Record<string, unknown>) => Tool)({
        sessionId: "session-1",
      });
      if ((tool as Tool & { name?: string }).name === "ov_archive_search") {
        archiveSearch = tool;
      }
    },
    getClient: vi.fn().mockResolvedValue({
      grepSessionArchives: vi.fn().mockResolvedValue({ count: matches.length, matches }),
    }),
    rememberSessionAgentId: vi.fn(),
    toOvSessionId: vi.fn(() => "session-1"),
    resolveAgentId: vi.fn(() => "agent-1"),
    resolvePluginSessionRouting: vi.fn(() => ({ agentId: "agent-1" })),
    isBypassedSession: vi.fn(() => false),
    makeBypassedToolResult: vi.fn(),
    formatMessage: vi.fn(() => ""),
    traceRecorder,
    traceRecallMaxResultsPerSearch: 20,
    traceRecallPreviewChars: 200,
    createTraceId: vi.fn(() => "trace-1"),
  };

  registerOpenVikingArchiveTools(deps);
  return { archiveSearch: archiveSearch!, traceRecorder };
}

describe("ov_archive_search", () => {
  it("hides stale memory-diff fields and labels displayed sources", async () => {
    const { archiveSearch, traceRecorder } = makeArchiveSearch([
      {
        uri: "viking://session/s/history/archive_001/memory_diff.json",
        line: 1,
        content: '"before": {"text": "stale value"}',
      },
      {
        uri: "viking://session/s/history/archive_001/memory_diff.json",
        line: 2,
        content: '"uri": "viking://user/default/memories/private"',
      },
      {
        uri: "viking://session/s/history/archive_001/memory_diff.json",
        line: 3,
        content: '"after": {"text": "current value"}',
      },
      {
        uri: "viking://session/s/history/archive_001/messages.jsonl",
        line: 4,
        content: "first original message",
      },
      {
        uri: "viking://session/s/history/archive_001/metadata.json",
        line: 5,
        content: "unknown metadata value",
      },
    ]);

    const result = await archiveSearch.execute("call-1", { query: "value" });
    const text = result.content[0]!.text;

    expect(text).toContain("source: messages.jsonl");
    expect(text).toContain("source: memory_diff.json");
    expect(text).toContain("field: after");
    expect(text.indexOf("source: messages.jsonl")).toBeLessThan(
      text.indexOf("source: memory_diff.json"),
    );
    expect(text).toContain("current value");
    expect(text).not.toContain("stale value");
    expect(text).not.toContain("memories/private");
    expect(text).not.toContain("unknown metadata value");
    expect(result.details).toMatchObject({
      matchCount: 2,
      rawMatchCount: 5,
      hiddenMatchCount: 3,
      shownMatchCount: 2,
    });
    expect(traceRecorder.recordAndFlush).toHaveBeenCalledWith(
      expect.objectContaining({
        selected: expect.arrayContaining([
          expect.objectContaining({ displayed: true }),
        ]),
        stats: expect.objectContaining({ candidateCount: 5, selectedCount: 2 }),
      }),
    );
    const trace = traceRecorder.recordAndFlush.mock.calls[0]![0];
    const tracedResults = JSON.stringify(trace.searches[0].results);
    expect(tracedResults).toContain("first original message");
    expect(tracedResults).toContain("current value");
    expect(tracedResults).not.toContain("stale value");
    expect(tracedResults).not.toContain("memories/private");
    expect(tracedResults).not.toContain("unknown metadata value");
  });

  it("gives multiple archives a chance before filling the five result slots", async () => {
    const matches = [
      ...Array.from({ length: 5 }, (_, index) => ({
        uri: "viking://session/s/history/archive_001/messages.jsonl",
        line: index + 1,
        content: `archive one message ${index + 1}`,
      })),
      ...Array.from({ length: 2 }, (_, index) => ({
        uri: "viking://session/s/history/archive_002/messages.jsonl",
        line: index + 1,
        content: `archive two message ${index + 1}`,
      })),
    ];
    const { archiveSearch } = makeArchiveSearch(matches);

    const result = await archiveSearch.execute("call-1", { query: "message" });
    const text = result.content[0]!.text;

    expect(result.details.shownMatchCount).toBe(5);
    expect(text).toContain("archive one message 3");
    expect(text).not.toContain("archive one message 4");
    expect(text).toContain("archive two message 1");
    expect(text).toContain("archive two message 2");
  });
});
