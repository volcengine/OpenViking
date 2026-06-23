import { describe, expect, it, vi } from "vitest";

import {
  convertToAgentMessages,
  ensureAlternation,
  formatMessageFaithful,
  mergeConsecutiveAssistants,
  mergeConsecutiveUsers,
  toRoleId,
} from "../../services/context-message-adapter.js";
import { assembleOpenVikingSession, afterTurnOpenVikingSession, commitOpenVikingSession, compactOpenVikingSession } from "../../services/context-lifecycle-service.js";
import { openClawSessionToOvStorageId } from "../../routing/identity-routing.js";

describe("context-engine message adapter seam", () => {
  it("sanitizes role ids in the concrete message adapter", () => {
    expect(toRoleId(" Alice:Build Bot ")).toBe("Alice_Build_Bot");
    expect(toRoleId("@#$")).toBeUndefined();
  });

  it("formats OpenViking messages in the concrete message adapter", () => {
    const message = {
      role: "assistant",
      parts: [
        { type: "text", text: "hello" },
        { type: "tool", tool_name: "read_file", tool_status: "completed", tool_input: { path: "a.ts" }, tool_output: "ok" },
        { type: "context", uri: "viking://mem/1", abstract: "remember this" },
      ],
    };

    expect(formatMessageFaithful(message as any)).toContain("[Tool: read_file] (completed)");
  });

  it("converts structured tool round-trips in the concrete message adapter", () => {
    const message = {
      role: "assistant",
      parts: [{
        type: "tool",
        tool_id: "toolu_1",
        tool_name: "search",
        tool_input: { query: "phase 5" },
        tool_output: "result text",
        tool_status: "completed",
      }],
    };

    expect(convertToAgentMessages(message)).toEqual([
      { role: "assistant", content: [{ type: "toolCall", id: "toolu_1", name: "search", arguments: { query: "phase 5" } }] },
      { role: "toolResult", toolCallId: "toolu_1", content: [{ type: "text", text: "result text" }], isError: false, toolName: "search" },
    ]);
  });

  it("normalizes role alternation in the concrete message adapter", () => {
    const messages = [
      { role: "assistant", content: [{ type: "text", text: "a" }] },
      { role: "assistant", content: [{ type: "text", text: "b" }] },
      { role: "user", content: "u1" },
      { role: "user", content: [{ type: "tool_result", content: "ok" }, { type: "text", text: "u2" }] },
      { role: "assistant", content: "c" },
      { role: "assistant", content: "d" },
    ];

    expect(mergeConsecutiveAssistants(messages)).toHaveLength(4);
    expect(mergeConsecutiveAssistants(messages)[0]).toEqual({
      role: "assistant",
      content: [{ type: "text", text: "a" }, { type: "text", text: "b" }],
    });
    expect(mergeConsecutiveUsers(messages)).toHaveLength(5);
    expect(mergeConsecutiveUsers(messages)[2]).toEqual({
      role: "user",
      content: [{ type: "tool_result", content: "ok" }, { type: "text", text: "u1" }, { type: "text", text: "u2" }],
    });
    expect(ensureAlternation(messages)).toEqual([
      { role: "assistant", content: [{ type: "text", text: "a" }] },
      { role: "user", content: "(no content)" },
      { role: "assistant", content: [{ type: "text", text: "b" }] },
      { role: "user", content: "u1" },
      { role: "user", content: [{ type: "tool_result", content: "ok" }, { type: "text", text: "u2" }] },
      { role: "assistant", content: "c" },
      { role: "user", content: "(no content)" },
      { role: "assistant", content: "d" },
    ]);
  });
});

describe("context-engine lifecycle service seam", () => {
  it("assembles through the lifecycle service seam and preserves no-data passthrough diagnostics", async () => {
    const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
    const messages = [{ role: "user", content: "hello world" }];
    const client = {
      getSessionContext: vi.fn().mockResolvedValue({
        latest_archive_overview: "",
        pre_archive_abstracts: [],
        messages: [],
      }),
    };
    const diag = vi.fn();
    const rememberSessionAgentId = vi.fn();
    const buildAssembledContext = vi.fn();

    const result = await assembleOpenVikingSession({
      sessionId: "plain-session",
      sessionKey: "agent:main:main",
      messages,
      tokenBudget: 4096,
      runtimeContext: { senderId: "telegram:123", agentId: "runtime-agent" },
      isMainAssemble: true,
      cfg: { autoRecall: false },
      getClient: vi.fn().mockResolvedValue(client),
      logger,
      resolveAgentId: vi.fn().mockReturnValue("agent_main"),
      rememberSessionAgentId,
      isBypassedSession: () => false,
      diag,
      roughEstimate: () => 42,
      messageDigest: () => [{ role: "user", content: "hello world", tokens: 42, truncated: false }],
      extractAgentMessageText: () => "hello world",
      hasAutoRecallBlock: () => false,
      prependRecallToLatestUserMessage: vi.fn(),
      buildAssembledContext,
    });

    const ovSessionId = openClawSessionToOvStorageId("plain-session", "agent:main:main");
    expect(rememberSessionAgentId).toHaveBeenCalledWith({
      sessionId: "plain-session",
      sessionKey: "agent:main:main",
      agentId: "runtime-agent",
      ovSessionId,
    });
    expect(client.getSessionContext).toHaveBeenCalledWith(ovSessionId, 4096, "agent_main");
    expect(buildAssembledContext).not.toHaveBeenCalled();
    expect(result).toEqual({ messages, estimatedTokens: 42 });
    expect(diag).toHaveBeenCalledWith("assemble_result", ovSessionId, expect.objectContaining({
      passthrough: true,
      reason: "no_ov_data",
      estimatedTokens: 42,
      archiveCount: 0,
      activeCount: 0,
    }));
  });

  it("commits an OpenViking session with stable identity, agent resolution, and memory-count logging", async () => {
    const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
    const client = {
      commitSession: vi.fn().mockResolvedValue({
        status: "completed",
        archived: true,
        memories_extracted: { core: 2, preference: 3 },
        task_id: "task-1",
        trace_id: "trace-1",
      }),
    };
    const getClient = vi.fn().mockResolvedValue(client);
    const rememberSessionAgentId = vi.fn();
    const resolveAgentId = vi.fn().mockReturnValue("agent_main");

    const ok = await commitOpenVikingSession({
      sessionId: "plain-session",
      sessionKey: "agent:main:main",
      getClient,
      logger,
      rememberSessionAgentId,
      resolveAgentId,
      isBypassedSession: () => false,
    });

    const ovSessionId = openClawSessionToOvStorageId("plain-session", "agent:main:main");
    expect(ok).toBe(true);
    expect(rememberSessionAgentId).toHaveBeenCalledWith({
      sessionId: "plain-session",
      sessionKey: "agent:main:main",
      ovSessionId,
    });
    expect(resolveAgentId).toHaveBeenCalledWith("plain-session", "agent:main:main", ovSessionId);
    expect(client.commitSession).toHaveBeenCalledWith(ovSessionId, {
      wait: true,
      agentId: "agent_main",
      keepRecentCount: 0,
    });
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("memories=5"));
  });

  it("compacts an OpenViking session behind the lifecycle service seam", async () => {
    const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
    const client = {
      getSessionContext: vi.fn()
        .mockResolvedValueOnce({ estimatedTokens: 321 })
        .mockResolvedValueOnce({
          latest_archive_overview: "  compact summary  ",
          estimatedTokens: 123,
          messages: [{ role: "user", content: "kept" }],
          pre_archive_abstracts: [],
        }),
      commitSession: vi.fn().mockResolvedValue({
        status: "completed",
        archived: true,
        archive_uri: "ov://archive/archive-9",
        memories_extracted: { core: 4 },
        task_id: "task-9",
        trace_id: "trace-9",
      }),
    };
    const diag = vi.fn();

    const result = await compactOpenVikingSession({
      sessionId: "plain-session",
      sessionKey: "agent:main:main",
      tokenBudget: 4096,
      currentTokenCount: undefined,
      force: true,
      compactionTarget: "budget",
      customInstructions: "keep decisions",
      getClient: vi.fn().mockResolvedValue(client),
      logger,
      resolveAgentId: vi.fn().mockReturnValue("agent_main"),
      isBypassedSession: () => false,
      diag,
    });

    const ovSessionId = openClawSessionToOvStorageId("plain-session", "agent:main:main");
    expect(client.getSessionContext).toHaveBeenNthCalledWith(1, ovSessionId, 4096, "agent_main");
    expect(client.commitSession).toHaveBeenCalledWith(ovSessionId, {
      wait: true,
      agentId: "agent_main",
      keepRecentCount: 0,
    });
    expect(result).toEqual({
      ok: true,
      compacted: true,
      reason: "commit_completed",
      result: {
        summary: "compact summary",
        firstKeptEntryId: "archive-9",
        tokensBefore: 321,
        tokensAfter: 123,
        details: {
          commit: {
            status: "completed",
            archived: true,
            archive_uri: "ov://archive/archive-9",
            memories_extracted: { core: 4 },
            task_id: "task-9",
            trace_id: "trace-9",
          },
        },
      },
    });
    expect(diag).toHaveBeenCalledWith("compact_entry", ovSessionId, expect.objectContaining({
      tokenBudget: 4096,
      force: true,
      compactionTarget: "budget",
      hasCustomInstructions: true,
    }));
    expect(diag).toHaveBeenCalledWith("compact_result", ovSessionId, expect.objectContaining({
      ok: true,
      compacted: true,
      reason: "commit_completed",
      memories: 4,
      tokensBefore: 321,
      tokensAfter: 123,
      latestArchiveId: "archive-9",
      summaryPresent: true,
    }));
  });

  it("captures afterTurn messages and commits behind the lifecycle service seam", async () => {
    const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
    const client = {
      addSessionMessage: vi.fn().mockResolvedValue(undefined),
      getSession: vi.fn().mockResolvedValue({ pending_tokens: 25000 }),
      commitSession: vi.fn().mockResolvedValue({
        status: "accepted",
        archived: false,
        task_id: "task-after-1",
        trace_id: "trace-after-1",
        memories_extracted: { core: 2 },
      }),
    };
    const diag = vi.fn();
    const rememberSessionAgentId = vi.fn();

    await afterTurnOpenVikingSession({
      sessionId: "plain-session",
      sessionKey: "agent:main:main",
      messages: [{
        role: "user",
        content: "hello <relevant-memories>hidden</relevant-memories> world",
        timestamp: 1775037660000,
      }],
      prePromptMessageCount: 0,
      tokenBudget: 100000,
      runtimeContext: { senderId: "telegram:123", agentId: "runtime-agent" },
      cfg: {
        autoCapture: true,
        commitTokenThresholdRatio: 0.2,
        commitKeepRecentCount: 7,
        logFindRequests: false,
      },
      getClient: vi.fn().mockResolvedValue(client),
      logger,
      resolveAgentId: vi.fn().mockReturnValue("agent_main"),
      rememberSessionAgentId,
      isBypassedSession: () => false,
      diag,
    });

    const ovSessionId = openClawSessionToOvStorageId("plain-session", "agent:main:main");
    expect(rememberSessionAgentId).toHaveBeenCalledWith({
      agentId: "runtime-agent",
      sessionId: "plain-session",
      sessionKey: "agent:main:main",
      ovSessionId,
    });
    expect(client.addSessionMessage).toHaveBeenCalledWith(
      ovSessionId,
      "user",
      [{ type: "text", text: "hello world" }],
      "agent_main",
      "2026-04-01T10:01:00.000Z",
      "telegram_123",
    );
    expect(client.getSession).toHaveBeenCalledWith(ovSessionId, "agent_main");
    expect(client.commitSession).toHaveBeenCalledWith(ovSessionId, {
      wait: false,
      agentId: "agent_main",
      keepRecentCount: 7,
    });
    expect(diag).toHaveBeenCalledWith("afterTurn_commit", ovSessionId, expect.objectContaining({
      pendingTokens: 25000,
      commitTokenThreshold: 20000,
      status: "accepted",
      archived: false,
      taskId: "task-after-1",
      extractedMemories: 2,
      senderIdFound: true,
      senderId: "telegram:123",
    }));
  });
});
