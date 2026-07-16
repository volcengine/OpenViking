import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";

function makeEngine(
  preArchiveAbstracts: Array<{ archive_id: string; abstract: string }>,
  messages: Array<Record<string, unknown>> = [],
) {
  const cfg = memoryOpenVikingConfigSchema.parse({
    mode: "remote",
    baseUrl: "http://127.0.0.1:1933",
    autoCapture: false,
    autoRecall: false,
  });
  const client = {
    getSessionContext: vi.fn().mockResolvedValue({
      latest_archive_overview: "The session discussed several topics.",
      pre_archive_abstracts: preArchiveAbstracts,
      messages,
      estimatedTokens: 100,
      stats: {
        totalArchives: preArchiveAbstracts.length,
        includedArchives: preArchiveAbstracts.length,
        droppedArchives: 0,
        failedArchives: 0,
        activeTokens: 0,
        archiveTokens: 100,
      },
    }),
  } as unknown as OpenVikingClient;

  return createMemoryOpenVikingContextEngine({
    id: "openviking",
    name: "Context Engine (OpenViking)",
    version: "test",
    cfg,
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    getClient: vi.fn().mockResolvedValue(client),
    resolveAgentId: vi.fn(() => "agent:session-1"),
  });
}

describe("context-engine archive index", () => {
  it("injects archive abstracts after the session history summary", async () => {
    const engine = makeEngine([
      {
        archive_id: "archive_007",
        abstract: "Tim and John discussed The Hobbit and signed basketball cards.",
      },
    ]);

    const result = await engine.assemble({
      sessionId: "session-1",
      messages: [{ role: "user", content: "Which books did Tim read?" }],
      availableTools: [],
    });

    expect(result.messages[0]).toEqual({
      role: "user",
      content: [
        {
          type: "text",
          text: "[Session History Summary]\nThe session discussed several topics.",
        },
        {
          type: "text",
          text:
            "[Archive Index]\n" +
            "archive_007: Tim and John discussed The Hobbit and signed basketball cards.",
        },
      ],
    });
    expect(result.systemPromptAddition).toContain("at most one follow-up archive search");
    expect(result.systemPromptAddition).not.toContain("at least 2 keyword variations");
  });

  it("keeps the 20 most recent archive abstracts", async () => {
    const engine = makeEngine(
      Array.from({ length: 22 }, (_, index) => ({
        archive_id: `archive_${String(index + 1).padStart(3, "0")}`,
        abstract: `Archive ${index + 1} details.`,
      })),
    );

    const result = await engine.assemble({
      sessionId: "session-1",
      messages: [{ role: "user", content: "What happened recently?" }],
      availableTools: [],
    });
    const archiveIndex = JSON.stringify(result.messages);

    expect(archiveIndex).toContain("[Archive Index]\\narchive_022:");
    expect(archiveIndex).toContain("archive_003: Archive 3 details.");
    expect(archiveIndex).not.toContain("archive_001:");
    expect(archiveIndex).not.toContain("archive_002:");
  });

  it("does not let a long Archive Index displace active messages", async () => {
    const engine = makeEngine(
      [{ archive_id: "archive_001", abstract: "x".repeat(4_000) }],
      [
        {
          id: "active-1",
          role: "user",
          created_at: "2026-07-11T00:00:00Z",
          parts: [{ type: "text", text: "active tail must remain" }],
        },
      ],
    );

    const result = await engine.assemble({
      sessionId: "session-1",
      messages: [{ role: "user", content: "fallback" }],
      tokenBudget: 1_000,
      availableTools: [],
    });
    const rendered = JSON.stringify(result.messages);

    expect(rendered).toContain("active tail must remain");
    expect(rendered).not.toContain("archive_001:");
  });
});
