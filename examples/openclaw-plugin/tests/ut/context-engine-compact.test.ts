import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import {
  categorizeCommitError,
  createMemoryOpenVikingContextEngine,
} from "../../context-engine.js";

function makeLogger() {
  return {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
}

function makeEngine(commitResult: unknown, opts?: { throwError?: Error }) {
  const cfg = memoryOpenVikingConfigSchema.parse({
    mode: "remote",
    baseUrl: "http://127.0.0.1:1933",
    autoCapture: false,
    autoRecall: false,
  });
  const logger = makeLogger();

  const commitSession = opts?.throwError
    ? vi.fn().mockRejectedValue(opts.throwError)
    : vi.fn().mockResolvedValue(commitResult);

  const client = {
    commitSession,
    getSessionContext: vi.fn().mockResolvedValue({
      latest_archive_overview: "",
      latest_archive_id: "",
      pre_archive_abstracts: [],
      messages: [],
      estimatedTokens: 0,
      stats: { totalArchives: 0, includedArchives: 0, droppedArchives: 0, failedArchives: 0, activeTokens: 0, archiveTokens: 0 },
    }),
  } as unknown as OpenVikingClient;

  const getClient = vi.fn().mockResolvedValue(client);
  const resolveAgentId = vi.fn((_sid: string) => "test-agent");

  const engine = createMemoryOpenVikingContextEngine({
    id: "openviking",
    name: "Test Engine",
    version: "test",
    cfg,
    logger,
    getClient,
    resolveAgentId,
  });

  return {
    engine,
    client: client as unknown as {
      commitSession: ReturnType<typeof vi.fn>;
    },
    logger,
  };
}

describe("context-engine commitOVSession()", () => {
  it("returns true on successful commit", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: { core: 1 },
    });

    const ok = await engine.commitOVSession("test-session");
    expect(ok).toBe(true);
  });

  it("returns false on failed commit", async () => {
    const { engine } = makeEngine({
      status: "failed",
      error: "extraction error",
    });

    const ok = await engine.commitOVSession("test-session");
    expect(ok).toBe(false);
  });

  it("returns false on timeout commit", async () => {
    const { engine } = makeEngine({
      status: "timeout",
      task_id: "task-timeout",
    });

    const ok = await engine.commitOVSession("test-session");
    expect(ok).toBe(false);
  });

  it("returns false when commit throws", async () => {
    const { engine } = makeEngine(null, {
      throwError: new Error("connection refused"),
    });

    const ok = await engine.commitOVSession("test-session");
    expect(ok).toBe(false);
  });

  it("uses wait=true for synchronous extraction", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: {},
    });

    await engine.commitOVSession("s1");

    expect(client.commitSession.mock.calls[0][1]).toMatchObject({ wait: true });
  });

  it("logs memories extracted count", async () => {
    const { engine, logger } = makeEngine({
      status: "completed",
      archived: true,
      memories_extracted: { core: 3, preferences: 1 },
    });

    await engine.commitOVSession("s1");

    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("memories=4"),
    );
  });

  it("skips commitOVSession when the session matches bypassSessionPatterns", async () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: false,
      autoRecall: false,
      bypassSessionPatterns: ["agent:*:cron:**"],
    });
    const logger = makeLogger();
    const getClient = vi.fn();
    const resolveAgentId = vi.fn((_sid: string) => "test-agent");

    const engine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "Test Engine",
      version: "test",
      cfg,
      logger,
      getClient: getClient as any,
      resolveAgentId,
    });

    const ok = await engine.commitOVSession("runtime-session", "agent:main:cron:nightly:run:1");

    expect(ok).toBe(false);
    expect(getClient).not.toHaveBeenCalled();
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("session is bypassed"),
    );
  });
});

describe("context-engine compact()", () => {
  it("returns compacted=false when the session matches bypassSessionPatterns", async () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: false,
      autoRecall: false,
      bypassSessionPatterns: ["agent:*:cron:**"],
    });
    const logger = makeLogger();
    const getClient = vi.fn();
    const resolveAgentId = vi.fn((_sid: string) => "test-agent");

    const engine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "Test Engine",
      version: "test",
      cfg,
      logger,
      getClient: getClient as any,
      resolveAgentId,
    });

    const result = await engine.compact({
      sessionId: "agent:main:cron:nightly:run:1",
      sessionFile: "",
    });

    expect(result).toEqual({
      ok: true,
      compacted: false,
      reason: "session_bypassed",
    });
    expect(getClient).not.toHaveBeenCalled();
  });

  it("returns compacted=true when commit succeeds with archived=true", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: true,
      task_id: "task-1",
      memories_extracted: { core: 3, preferences: 1 },
    });

    const result = await engine.compact({
      sessionId: "s1",
      sessionFile: "",
    });

    expect(result.ok).toBe(true);
    expect(result.compacted).toBe(true);
    expect(result.reason).toBe("commit_completed");
  });

  it("returns compacted=false when commit succeeds with archived=false", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: false,
      task_id: "task-2",
      memories_extracted: {},
    });

    const result = await engine.compact({
      sessionId: "s2",
      sessionFile: "",
    });

    expect(result.ok).toBe(true);
    expect(result.compacted).toBe(false);
    expect(result.reason).toBe("commit_no_archive");
  });

  it("returns ok=false when commit status is 'failed'", async () => {
    const { engine, logger } = makeEngine({
      status: "failed",
      error: "extraction pipeline error",
      task_id: "task-3",
    });

    const result = await engine.compact({
      sessionId: "s3",
      sessionFile: "",
    });

    expect(result.ok).toBe(false);
    expect(result.compacted).toBe(false);
    expect(result.reason).toBe("commit_failed");
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("Phase 2 failed"),
    );
  });

  it("returns ok=false when commit status is 'timeout'", async () => {
    const { engine, logger } = makeEngine({
      status: "timeout",
      task_id: "task-4",
    });

    const result = await engine.compact({
      sessionId: "s4",
      sessionFile: "",
    });

    expect(result.ok).toBe(false);
    expect(result.compacted).toBe(false);
    expect(result.reason).toBe("commit_timeout");
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("Phase 2 timed out"),
    );
  });

  it("commit passes wait=true for synchronous extraction", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: true,
      memories_extracted: { core: 2 },
    });

    await engine.compact({ sessionId: "s1", sessionFile: "" });

    expect(client.commitSession).toHaveBeenCalledTimes(1);
    expect(client.commitSession.mock.calls[0][1]).toMatchObject({ wait: true });
  });

  it("logs memory extraction count on success", async () => {
    const { engine, logger } = makeEngine({
      status: "completed",
      archived: true,
      task_id: "task-mem",
      memories_extracted: { core: 5, preferences: 2 },
    });

    await engine.compact({ sessionId: "s1", sessionFile: "" });

    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("memories=7"),
    );
  });

  it("handles commit with zero memories extracted", async () => {
    const { engine, logger } = makeEngine({
      status: "completed",
      archived: true,
      task_id: "task-empty",
      memories_extracted: {},
    });

    await engine.compact({ sessionId: "s-empty", sessionFile: "" });

    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("memories=0"),
    );
  });

  it("handles commit with missing memories_extracted field", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: false,
    });

    const result = await engine.compact({ sessionId: "s-no-mem", sessionFile: "" });
    expect(result.ok).toBe(true);
  });

  it("uses correct OV session ID derived from sessionId", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: {},
    });

    await engine.compact({
      sessionId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      sessionFile: "",
    });

    const commitCallSessionId = client.commitSession.mock.calls[0][0] as string;
    expect(commitCallSessionId).toBe("a1b2c3d4-e5f6-7890-abcd-ef1234567890");
  });

  it("passes agentId to commitSession", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: {},
    });

    await engine.compact({ sessionId: "s1", sessionFile: "" });

    expect(client.commitSession.mock.calls[0][1]).toMatchObject({
      agentId: "test-agent",
    });
  });

  it("returns reason=commit_error for uncategorizable commit failures", async () => {
    const { engine, logger } = makeEngine(null, {
      throwError: new Error("opaque problem"),
    });

    const result = await engine.compact({
      sessionId: "s5",
      sessionFile: "",
    });

    expect(result.ok).toBe(false);
    expect(result.compacted).toBe(false);
    expect(result.reason).toBe("commit_error");
    expect((result.result?.details as { category?: string })?.category).toBe("unknown");
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("commit failed"),
    );
  });

  it("enriches reason with error category for HTTP failures", async () => {
    const { engine } = makeEngine(null, {
      throwError: new Error("OpenViking request failed: HTTP 500"),
    });

    const result = await engine.compact({
      sessionId: "s6",
      sessionFile: "",
    });

    expect(result.ok).toBe(false);
    expect(result.reason).toBe("commit_error: HTTP 500");
    expect((result.result?.details as { category?: string })?.category).toBe("HTTP 500");
  });

  it("enriches reason with OV error code when bracketed", async () => {
    const { engine } = makeEngine(null, {
      throwError: new Error("OpenViking request failed [PERMISSION_DENIED]: forbidden"),
    });

    const result = await engine.compact({
      sessionId: "s7",
      sessionFile: "",
    });

    expect(result.reason).toBe("commit_error: PERMISSION_DENIED");
  });
});

describe("categorizeCommitError", () => {
  it("extracts bracketed OV error code", () => {
    expect(
      categorizeCommitError(new Error("OpenViking request failed [NOT_FOUND]: Session not found")),
    ).toBe("NOT_FOUND");
    expect(
      categorizeCommitError(new Error("OpenViking request failed [INTERNAL_ERROR]: boom")),
    ).toBe("INTERNAL_ERROR");
  });

  it("extracts HTTP status when no bracketed code is present", () => {
    expect(categorizeCommitError(new Error("OpenViking request failed: HTTP 422"))).toBe("HTTP 422");
    expect(categorizeCommitError(new Error("HTTP 503 Service Unavailable"))).toBe("HTTP 503");
  });

  it("classifies timeout and network errors", () => {
    expect(categorizeCommitError(new Error("commit timeout"))).toBe("timeout");
    expect(categorizeCommitError(new Error("fetch failed: ECONNREFUSED"))).toBe("network_error");
    expect(categorizeCommitError(new Error("getaddrinfo ENOTFOUND"))).toBe("network_error");
  });

  it("falls back to unknown when no category matches", () => {
    expect(categorizeCommitError(new Error("some strange problem"))).toBe("unknown");
    expect(categorizeCommitError("plain string with no cues")).toBe("unknown");
  });
});

describe("context-engine compact() dormant-session self-heal", () => {
  function makeSelfHealEngine(params: {
    commitResponses: Array<Error | unknown>;
  }) {
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: false,
      autoRecall: false,
    });
    const logger = makeLogger();

    const commitSession = vi.fn();
    for (const response of params.commitResponses) {
      if (response instanceof Error) {
        commitSession.mockRejectedValueOnce(response);
      } else {
        commitSession.mockResolvedValueOnce(response);
      }
    }

    const addSessionMessage = vi.fn().mockResolvedValue(undefined);

    const client = {
      commitSession,
      addSessionMessage,
      getSessionContext: vi.fn().mockResolvedValue({
        latest_archive_overview: "",
        latest_archive_id: "",
        pre_archive_abstracts: [],
        messages: [],
        estimatedTokens: 0,
        stats: { totalArchives: 0, includedArchives: 0, droppedArchives: 0, failedArchives: 0, activeTokens: 0, archiveTokens: 0 },
      }),
    } as unknown as OpenVikingClient;

    const getClient = vi.fn().mockResolvedValue(client);
    const resolveAgentId = vi.fn((_sid: string) => "test-agent");

    const engine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "Test Engine",
      version: "test",
      cfg,
      logger,
      getClient,
      resolveAgentId,
    });

    return {
      engine,
      commitSession,
      addSessionMessage,
      logger,
    };
  }

  it("seeds and retries commit once when server returns NOT_FOUND", async () => {
    const notFoundErr = new Error(
      "OpenViking request failed [NOT_FOUND]: Session not found: s-dormant",
    );
    const { engine, commitSession, addSessionMessage, logger } = makeSelfHealEngine({
      commitResponses: [
        notFoundErr,
        {
          status: "completed",
          archived: true,
          task_id: "task-seed",
          memories_extracted: {},
          archive_uri: "viking://session/s-dormant/history/archive_001",
        },
      ],
    });

    const result = await engine.compact({ sessionId: "s-dormant", sessionFile: "" });

    expect(result.ok).toBe(true);
    expect(result.reason).toBe("commit_completed");
    expect(commitSession).toHaveBeenCalledTimes(2);
    expect(addSessionMessage).toHaveBeenCalledTimes(1);
    expect(addSessionMessage.mock.calls[0][0]).toBe("s-dormant");
    expect(addSessionMessage.mock.calls[0][1]).toBe("user");
    const parts = addSessionMessage.mock.calls[0][2] as Array<{ type: string; text: string }>;
    expect(parts).toHaveLength(1);
    expect(parts[0].type).toBe("text");
    expect(parts[0].text).toContain("dormant-seed");
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("seeding dormant session"),
    );
  });

  it("does not seed or retry when commit throws a non-NOT_FOUND error", async () => {
    const { engine, commitSession, addSessionMessage } = makeSelfHealEngine({
      commitResponses: [new Error("fetch failed: ECONNREFUSED")],
    });

    const result = await engine.compact({ sessionId: "s-net", sessionFile: "" });

    expect(result.ok).toBe(false);
    expect(result.reason).toBe("commit_error: network_error");
    expect(commitSession).toHaveBeenCalledTimes(1);
    expect(addSessionMessage).not.toHaveBeenCalled();
  });

  it("returns commit_error with category when the retry after seeding also fails", async () => {
    const notFoundErr = new Error(
      "OpenViking request failed [NOT_FOUND]: Session not found: s-double",
    );
    const retryErr = new Error("OpenViking request failed: HTTP 500");
    const { engine, commitSession, addSessionMessage } = makeSelfHealEngine({
      commitResponses: [notFoundErr, retryErr],
    });

    const result = await engine.compact({ sessionId: "s-double", sessionFile: "" });

    expect(result.ok).toBe(false);
    expect(result.reason).toBe("commit_error: HTTP 500");
    expect(commitSession).toHaveBeenCalledTimes(2);
    expect(addSessionMessage).toHaveBeenCalledTimes(1);
  });
});
