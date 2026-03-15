import { describe, expect, it, vi } from "vitest";

import { createTools } from "./tools.js";

describe("tools", () => {
  it("commit_memory preserves commit error when deleteSession also fails", async () => {
    const tools = createTools({
      client: {
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => {
          throw new Error("commit failed");
        }),
        deleteSession: vi.fn(async () => {
          throw new Error("delete failed");
        }),
        find: vi.fn(async () => ({ memories: [] })),
        health: vi.fn(async () => true),
        baseUrl: "http://127.0.0.1:1933",
      },
    } as never);

    const commitTool = tools.find((tool) => tool.name === "commit_memory");
    expect(commitTool).toBeDefined();
    await expect(
      commitTool!.execute("tc1", {
        content: "remember this",
      }),
    ).rejects.toThrow("commit failed");
  });

  it("commit_memory accepts extended fields and writes structured content", async () => {
    const createSession = vi.fn(async () => "s1");
    const addSessionMessage = vi.fn(async () => undefined);
    const commitSession = vi.fn(async () => ({ extractedCount: 2 }));
    const deleteSession = vi.fn(async () => undefined);

    const tools = createTools({
      client: {
        createSession,
        addSessionMessage,
        commitSession,
        deleteSession,
        find: vi.fn(async () => ({ memories: [] })),
        health: vi.fn(async () => true),
        baseUrl: "http://127.0.0.1:1933",
      },
    } as never);

    const commitTool = tools.find((tool) => tool.name === "commit_memory");
    expect(commitTool).toBeDefined();
    const result = await commitTool!.execute("tc1", {
      memory_content: "Remember that user likes concise replies.",
      memory_type: "preferences",
      priority: 4,
      category: "style",
      targetUri: "viking://user/memories/preferences/",
      role: "user",
    });

    expect(createSession).toHaveBeenCalledTimes(1);
    expect(commitSession).toHaveBeenCalledTimes(1);
    expect(deleteSession).toHaveBeenCalledTimes(1);
    expect(addSessionMessage).toHaveBeenCalledTimes(1);
    const committedText = String(addSessionMessage.mock.calls[0]?.[2] ?? "");
    expect(committedText).toContain("[openviking_memory_commit]");
    expect(committedText).toContain("memory_type: preferences");
    expect(committedText).toContain("priority: 4");
    expect(committedText).toContain("category: style");
    expect(committedText).toContain("target_uri: viking://user/memories/preferences/");
    expect(committedText).toContain("memory_content:");
    expect(committedText).toContain("Remember that user likes concise replies.");
    expect(JSON.stringify(result.details)).toContain("preferences");
  });

  it("commit_memory rejects empty payload when both content fields are missing", async () => {
    const tools = createTools({
      client: {
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 1 })),
        deleteSession: vi.fn(async () => undefined),
        find: vi.fn(async () => ({ memories: [] })),
        health: vi.fn(async () => true),
        baseUrl: "http://127.0.0.1:1933",
      },
    } as never);

    const commitTool = tools.find((tool) => tool.name === "commit_memory");
    expect(commitTool).toBeDefined();
    await expect(commitTool!.execute("tc1", {} as never)).rejects.toThrow(
      /memory_content/i,
    );
  });
});
