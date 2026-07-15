import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { OpenVikingClient } from "../../client.js";
import type { ResourcePackager } from "../../adapters/resource-packager.js";
import { isMemoryUri } from "../../routing/memory-uri.js";

function okResponse(result: unknown): Response {
  return new Response(JSON.stringify({ status: "ok", result }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function errorResponse(message: string, code = "INVALID_ARGUMENT"): Response {
  return new Response(JSON.stringify({ status: "error", error: { code, message } }), {
    status: 400,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("isMemoryUri", () => {
  it("returns true for valid user memory URI", () => {
    expect(isMemoryUri("viking://user/memories/abc-123")).toBe(true);
  });

  it("returns true for user memory URI with space prefix", () => {
    expect(isMemoryUri("viking://user/default/memories/item-1")).toBe(true);
  });

  it("returns true for user memory URI isolated by agent", () => {
    expect(isMemoryUri("viking://user/alice/agent/work/memories/item-1")).toBe(true);
  });

  it("returns true for valid agent memory URI", () => {
    expect(isMemoryUri("viking://user/memories/xyz")).toBe(true);
  });

  it("returns true for agent memory URI with space prefix", () => {
    expect(isMemoryUri("viking://user/abc123/memories/item-2")).toBe(true);
  });

  it("returns true for agent memory URI isolated by user", () => {
    expect(isMemoryUri("viking://user/work/memories/item-2")).toBe(true);
  });

  it("returns true for user memories root", () => {
    expect(isMemoryUri("viking://user/memories")).toBe(true);
  });

  it("returns true for user memories trailing slash", () => {
    expect(isMemoryUri("viking://user/memories/")).toBe(true);
  });

  it("returns false for user skills URI", () => {
    expect(isMemoryUri("viking://user/skills/abc")).toBe(false);
  });

  it("returns false for agent instructions URI", () => {
    expect(isMemoryUri("viking://user/instructions/rule-1")).toBe(false);
  });

  it("returns false for empty string", () => {
    expect(isMemoryUri("")).toBe(false);
  });

  it("returns false for random URL", () => {
    expect(isMemoryUri("http://example.com/memories")).toBe(false);
  });

  it("returns false for partial viking URI without scope", () => {
    expect(isMemoryUri("viking://memories/abc")).toBe(false);
  });
});

describe("OpenVikingClient resource and skill import", () => {
  it("addResource posts remote URL as path", async () => {
    const transport = vi.fn().mockResolvedValue(
      okResponse({ root_uri: "viking://resources/site", status: "success" }),
    );

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000, "", "", undefined, false, true, { transport });
    const result = await client.addResource({
      pathOrUrl: "https://example.com/docs",
      to: "viking://resources/site",
      wait: true,
    });

    expect(result.root_uri).toBe("viking://resources/site");
    expect(transport).toHaveBeenCalledTimes(1);
    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({
      path: "https://example.com/docs",
      to: "viking://resources/site",
      wait: true,
    });
  });

  it("addResource uploads local file before posting temp_file_id", async () => {
    const tempDir = await mkdtemp(join(tmpdir(), "ov-client-test-"));
    const filePath = join(tempDir, "resource.md");
    await writeFile(filePath, "# Demo\n");
    const transport = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_resource.md" }))
      .mockResolvedValueOnce(okResponse({
        root_uri: "viking://resources/demo",
        status: "success",
        queue_status: { completed: true },
      }));

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000, "", "", undefined, false, true, { transport });
    const result = await client.addResource({ pathOrUrl: filePath, wait: true });

    expect(result.queue_status).toEqual({ completed: true });
    expect(transport).toHaveBeenCalledTimes(2);
    expect(transport.mock.calls[0]![0]).toBe("http://127.0.0.1:1933/api/v1/resources/temp_upload");
    expect((transport.mock.calls[0]![1] as RequestInit).body).toBeInstanceOf(FormData);
    expect(JSON.parse(String((transport.mock.calls[1]![1] as RequestInit).body))).toMatchObject({
      temp_file_id: "upload_resource.md",
      wait: true,
    });
  });

  it("addResource zips local directory before upload", async () => {
    const dirPath = "/workspace/resource-dir";
    const uploadBody = new FormData();
    uploadBody.append("file", new Blob(["zip-bytes"]), "resource-dir.zip");
    const packaged = {
      kind: "upload" as const,
      uploadPath: "/virtual/resource-dir.zip",
      sourceName: "resource-dir",
      cleanupPath: "/virtual/resource-dir.zip",
    };
    const resourcePackager: ResourcePackager = {
      prepareResourceSource: vi.fn().mockResolvedValue(packaged),
      prepareLocalUploadSource: vi.fn(),
      createTempUploadBody: vi.fn().mockResolvedValue(uploadBody),
      cleanup: vi.fn(),
    };
    const transport = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_resource.zip" }))
      .mockResolvedValueOnce(okResponse({ root_uri: "viking://resources/resource-dir" }));

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000, "", "", undefined, false, true, { transport, resourcePackager });
    await client.addResource({ pathOrUrl: dirPath });

    expect(resourcePackager.prepareResourceSource).toHaveBeenCalledWith(dirPath);
    expect(resourcePackager.createTempUploadBody).toHaveBeenCalledWith("/virtual/resource-dir.zip");
    expect(resourcePackager.cleanup).toHaveBeenCalledWith(packaged);
    expect(transport).toHaveBeenCalledTimes(2);
    expect((transport.mock.calls[0]![1] as RequestInit).body).toBeInstanceOf(FormData);
    expect(JSON.parse(String((transport.mock.calls[1]![1] as RequestInit).body))).toMatchObject({
      temp_file_id: "upload_resource.zip",
      source_name: "resource-dir",
    });
  });

  it("addSkill uploads local SKILL.md file", async () => {
    const tempDir = await mkdtemp(join(tmpdir(), "ov-client-test-"));
    const filePath = join(tempDir, "SKILL.md");
    await writeFile(filePath, "---\nname: demo\ndescription: demo\n---\n\n# Demo\n");
    const transport = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_skill.md" }))
      .mockResolvedValueOnce(okResponse({ uri: "viking://user/skills/demo", name: "demo" }));

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000, "", "", undefined, false, true, { transport });
    const result = await client.addSkill({ path: filePath, wait: true });

    expect(result.uri).toBe("viking://user/skills/demo");
    expect(transport).toHaveBeenCalledTimes(2);
    expect(transport.mock.calls[0]![0]).toBe("http://127.0.0.1:1933/api/v1/resources/temp_upload");
    expect((transport.mock.calls[0]![1] as RequestInit).body).toBeInstanceOf(FormData);
    expect(JSON.parse(String((transport.mock.calls[1]![1] as RequestInit).body))).toMatchObject({
      temp_file_id: "upload_skill.md",
      wait: true,
    });
  });

  it("addSkill removes temporary zip directory after uploading a skill directory", async () => {
    const dirPath = "/workspace/skill-dir";
    const uploadBody = new FormData();
    uploadBody.append("file", new Blob(["zip-bytes"]), "skill-dir.zip");
    const packaged = {
      kind: "upload" as const,
      uploadPath: "/virtual/skill-dir.zip",
      cleanupPath: "/virtual/skill-dir.zip",
    };
    const resourcePackager: ResourcePackager = {
      prepareResourceSource: vi.fn(),
      prepareLocalUploadSource: vi.fn().mockResolvedValue(packaged),
      createTempUploadBody: vi.fn().mockResolvedValue(uploadBody),
      cleanup: vi.fn(),
    };
    const transport = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_skill.zip" }))
      .mockResolvedValueOnce(okResponse({ uri: "viking://user/skills/demo", name: "demo" }));

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000, "", "", undefined, false, true, { transport, resourcePackager });
    await client.addSkill({ path: dirPath, wait: true });

    expect(resourcePackager.prepareLocalUploadSource).toHaveBeenCalledWith(dirPath);
    expect(resourcePackager.createTempUploadBody).toHaveBeenCalledWith("/virtual/skill-dir.zip");
    expect(resourcePackager.cleanup).toHaveBeenCalledWith(packaged);
    expect(transport).toHaveBeenCalledTimes(2);
    expect((transport.mock.calls[0]![1] as RequestInit).body).toBeInstanceOf(FormData);
    expect(JSON.parse(String((transport.mock.calls[1]![1] as RequestInit).body))).toMatchObject({
      temp_file_id: "upload_skill.zip",
      wait: true,
    });
  });

  it("addSkill posts raw skill data directly", async () => {
    const data = "---\nname: inline\ndescription: inline\n---\n\n# Inline\n";
    const transport = vi.fn().mockResolvedValue(
      okResponse({ uri: "viking://user/skills/inline", name: "inline" }),
    );

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000, "", "", undefined, false, true, { transport });
    await client.addSkill({ data });

    expect(transport).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String((transport.mock.calls[0]![1] as RequestInit).body))).toMatchObject({
      data,
      wait: false,
    });
  });

  it("addSkill posts MCP tool dict directly", async () => {
    const data = {
      name: "demo_tool",
      description: "demo",
      inputSchema: { type: "object", properties: {} },
    };
    const transport = vi.fn().mockResolvedValue(
      okResponse({ uri: "viking://user/skills/demo-tool", name: "demo-tool" }),
    );

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000, "", "", undefined, false, true, { transport });
    await client.addSkill({ data });

    expect(JSON.parse(String((transport.mock.calls[0]![1] as RequestInit).body))).toMatchObject({
      data,
    });
  });

  it("surfaces OpenViking error responses", async () => {
    const transport = vi.fn().mockResolvedValue(errorResponse("bad import"));

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000, "", "", undefined, false, true, { transport });
    await expect(client.addResource({ pathOrUrl: "https://example.com/bad" })).rejects.toThrow(
      "OpenViking request failed [INVALID_ARGUMENT]: bad import",
    );
  });

  it("uses an extended request timeout for wait=true imports", async () => {
    vi.useFakeTimers();
    const transport = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      setTimeout(() => {
        resolve(okResponse({ root_uri: "viking://resources/site", status: "success" }));
      }, 20_000);
    }));

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 15_000, "", "", undefined, false, true, { transport });
    const pending = client.addResource({
      pathOrUrl: "https://example.com/docs",
      wait: true,
      timeout: 60,
    });

    await vi.advanceTimersByTimeAsync(20_000);

    await expect(pending).resolves.toMatchObject({
      root_uri: "viking://resources/site",
      status: "success",
    });
  });

  it("still uses the default request timeout for non-wait imports", async () => {
    vi.useFakeTimers();
    const transport = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      setTimeout(() => {
        resolve(okResponse({ root_uri: "viking://resources/site", status: "success" }));
      }, 20_000);
    }));

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 15_000, "", "", undefined, false, true, { transport });
    const pending = client.addResource({
      pathOrUrl: "https://example.com/docs",
      wait: false,
    });
    const assertion = expect(pending).rejects.toThrow(/aborted/i);

    await vi.advanceTimersByTimeAsync(15_001);

    await assertion;
  });

  it("keeps polling wait=true commit long enough for slow Phase 2 completion", async () => {
    vi.useFakeTimers();
    const transport = vi.fn((url: string) => {
      if (url.endsWith("/api/v1/sessions/slow-session/commit")) {
        return Promise.resolve(okResponse({
          session_id: "slow-session",
          status: "accepted",
          task_id: "task-slow",
          archived: true,
        }));
      }
      if (url.endsWith("/api/v1/tasks/task-slow")) {
        const completed = Date.now() >= 200_000;
        return Promise.resolve(okResponse({
          task_id: "task-slow",
          task_type: "session_commit",
          status: completed ? "completed" : "running",
          created_at: 0,
          updated_at: 0,
          result: completed ? { memories_extracted: { core: 1 } } : {},
        }));
      }
      throw new Error(`Unexpected URL: ${url}`);
    });

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5_000, "", "", undefined, false, true, { transport });
    const pending = client.commitSession("slow-session", { wait: true });

    await vi.advanceTimersByTimeAsync(200_500);

    await expect(pending).resolves.toMatchObject({
      status: "completed",
      archived: true,
      task_id: "task-slow",
      memories_extracted: { core: 1 },
    });
  });
});

describe("OpenVikingClient tenant headers (advanced accountId / userId overrides)", () => {
  it.each([
    ["prefix", "prefix_main"],
    ["", "main"],
  ])("sends OpenClaw default agent for health checks with prefix %j", async (prefix, expected) => {
    const transport = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));

    const client = new OpenVikingClient("http://127.0.0.1:1933", "sk-test", prefix, 5000, "", "", undefined, false, true, { transport });
    await client.healthCheck();

    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Actor-Peer")).toBe(expected);
  });

  it("sends explicitly configured accountId and userId in request headers", async () => {
    const transport = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "sk-test", "agent", 5000,
      "acct-123", "user-456", undefined, false, true, { transport },
    );
    await client.healthCheck();

    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Account")).toBe("acct-123");
    expect(headers.get("X-OpenViking-User")).toBe("user-456");
  });

  it("keeps api_key user-key flow free of explicit tenant overrides when accountId/userId are not configured", async () => {
    const transport = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "sk-user", "agent", 5000,
      "", "", undefined, false, true, { transport },
    );
    await client.healthCheck();

    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Account")).toBeNull();
    expect(headers.get("X-OpenViking-User")).toBeNull();
    expect(headers.get("X-API-Key")).toBe("sk-user");
  });

  it("preserves explicit tenant headers for api_key root-key style flows", async () => {
    const transport = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "sk-root", "agent", 5000,
      "acct-123", "user-456", undefined, false, true, { transport },
    );
    await client.healthCheck();

    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-API-Key")).toBe("sk-root");
    expect(headers.get("X-OpenViking-Account")).toBe("acct-123");
    expect(headers.get("X-OpenViking-User")).toBe("user-456");
  });

  it("applies configured headers after explicit API and tenant headers", async () => {
    const transport = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "sk-explicit", "agent", 5000,
      "acct-explicit", "user-explicit", undefined, false, true,
      {
        transport,
        headers: {
          "X-API-Key": "sk-from-config",
          "X-OpenViking-Account": "acct-from-config",
          "X-OpenViking-User": "user-from-config",
          openviking: "i18n-instance",
          token: "root-token",
        },
      },
    );
    await client.healthCheck();

    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-API-Key")).toBe("sk-from-config");
    expect(headers.get("X-OpenViking-Account")).toBe("acct-from-config");
    expect(headers.get("X-OpenViking-User")).toBe("user-from-config");
    expect(headers.get("openviking")).toBe("i18n-instance");
    expect(headers.get("token")).toBe("root-token");
  });

  it("can use explicit configured headers as the only API key source", async () => {
    const transport = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "", "agent", 5000,
      "", "", undefined, false, true,
      { transport, headers: { "X-API-Key": "sk-from-config", token: "root-token" } },
    );
    await client.healthCheck();

    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-API-Key")).toBe("sk-from-config");
    expect(headers.get("token")).toBe("root-token");
  });

  it("does not synthesize tenant headers when apiKey is missing", async () => {
    const transport = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "", "agent", 5000,
      "", "", undefined, false, true, { transport },
    );
    await client.healthCheck();

    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Account")).toBeNull();
    expect(headers.get("X-OpenViking-User")).toBeNull();
  });

  it("trims whitespace from accountId and userId overrides", async () => {
    const transport = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "", "agent", 5000,
      "  acct  ", "  user  ", undefined, false, true, { transport },
    );
    await client.healthCheck();

    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Account")).toBe("acct");
    expect(headers.get("X-OpenViking-User")).toBe("user");
  });
});

describe("OpenVikingClient canonical namespace policy", () => {
  it("keeps user memory alias unchanged and routes by actor peer by default", async () => {
    const transport = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "alice" });
      }
      if (url.endsWith("/api/v1/search/find")) {
        return okResponse({ memories: [], total: 0 });
      }
      return okResponse({});
    });

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "", "my-agent", 5000,
      "", "", undefined,
      false,
      true,
      { transport },
    );
    await client.find("test query", { targetUri: "viking://user/memories" }, "my-agent");

    const findCall = transport.mock.calls.find((c) =>
      String(c[0]).endsWith("/api/v1/search/find"),
    )!;
    const body = JSON.parse(String((findCall[1] as RequestInit).body));
    const headers = new Headers((findCall[1] as RequestInit).headers);
    expect(body.target_uri).toBe("viking://user/memories");
    expect(headers.get("X-OpenViking-Actor-Peer")).toBe("my-agent");
  });

  it("ignores legacy isolateUserScopeByAgent and routes by actor peer", async () => {
    const transport = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "alice" });
      }
      if (url.endsWith("/api/v1/search/find")) {
        return okResponse({ memories: [], total: 0 });
      }
      return okResponse({});
    });

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "", "my-agent", 5000,
      "", "", undefined,
      true,
      true,
      { transport },
    );
    await client.find("test query", { targetUri: "viking://user/memories" }, "my-agent");

    const findCall = transport.mock.calls.find((c) =>
      String(c[0]).endsWith("/api/v1/search/find"),
    )!;
    const body = JSON.parse(String((findCall[1] as RequestInit).body));
    const headers = new Headers((findCall[1] as RequestInit).headers);
    expect(body.target_uri).toBe("viking://user/memories");
    expect(headers.get("X-OpenViking-Actor-Peer")).toBe("my-agent");
  });

  it("expands agent memory alias to canonical agent/user root by default", async () => {
    const transport = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "alice" });
      }
      if (url.endsWith("/api/v1/search/find")) {
        return okResponse({ memories: [], total: 0 });
      }
      return okResponse({});
    });

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "", "shared-agent", 5000,
      "", "", undefined,
      false,
      true,
      { transport },
    );
    await client.find("test", { targetUri: "viking://user/memories" }, "shared-agent");

    const findCall = transport.mock.calls.find((c) =>
      String(c[0]).endsWith("/api/v1/search/find"),
    )!;
    const body = JSON.parse(String((findCall[1] as RequestInit).body));
    expect(body.target_uri).toBe("viking://user/memories");
  });

  it("keeps user skill target URI unchanged while using actor peer routing", async () => {
    const transport = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "alice" });
      }
      if (url.endsWith("/api/v1/search/find")) {
        return okResponse({ memories: [], total: 0 });
      }
      return okResponse({});
    });

    const client = new OpenVikingClient(
      "http://127.0.0.1:1933", "", "shared-agent", 5000,
      "", "", undefined,
      false,
      false,
      { transport },
    );
    await client.find("test", { targetUri: "viking://user/skills" }, "shared-agent");

    const findCall = transport.mock.calls.find((c) =>
      String(c[0]).endsWith("/api/v1/search/find"),
    )!;
    const body = JSON.parse(String((findCall[1] as RequestInit).body));
    expect(body.target_uri).toBe("viking://user/skills");
  });

  it("includes peer_id when addSessionMessage receives one", async () => {
    const transport = vi.fn().mockResolvedValue(okResponse({ session_id: "s1" }));

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000, "", "", undefined, false, true, { transport });
    await client.addSessionMessage(
      "s1",
      "user",
      [{ type: "text", text: "hello" }],
      "agent",
      "2026-04-20T00:00:00.000Z",
      "telegram_12345",
    );

    const [, init] = transport.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.peer_id).toBe("telegram_12345");
    expect(body).not.toHaveProperty("role_id");
  });
});
