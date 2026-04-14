import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { OpenVikingClient, isMemoryUri } from "../../client.js";

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

  it("returns true for valid agent memory URI", () => {
    expect(isMemoryUri("viking://agent/memories/xyz")).toBe(true);
  });

  it("returns true for agent memory URI with space prefix", () => {
    expect(isMemoryUri("viking://agent/abc123/memories/item-2")).toBe(true);
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
    expect(isMemoryUri("viking://agent/instructions/rule-1")).toBe(false);
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
    const fetchMock = vi.fn().mockResolvedValue(
      okResponse({ root_uri: "viking://resources/site", status: "success" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000);
    const result = await client.addResource({
      pathOrUrl: "https://example.com/docs",
      to: "viking://resources/site",
      wait: true,
    });

    expect(result.root_uri).toBe("viking://resources/site");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
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
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_resource.md" }))
      .mockResolvedValueOnce(okResponse({
        root_uri: "viking://resources/demo",
        status: "success",
        queue_status: { completed: true },
      }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000);
    const result = await client.addResource({ pathOrUrl: filePath, wait: true });

    expect(result.queue_status).toEqual({ completed: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]![0]).toBe("http://127.0.0.1:1933/api/v1/resources/temp_upload");
    expect((fetchMock.mock.calls[0]![1] as RequestInit).body).toBeInstanceOf(FormData);
    expect(JSON.parse(String((fetchMock.mock.calls[1]![1] as RequestInit).body))).toMatchObject({
      temp_file_id: "upload_resource.md",
      wait: true,
    });
  });

  it("addResource zips local directory before upload", async () => {
    const tempDir = await mkdtemp(join(tmpdir(), "ov-client-test-"));
    const dirPath = join(tempDir, "resource-dir");
    const uploadPrefix = "openviking-openclaw-upload-";
    const beforeDirs = (await readdir(tmpdir())).filter((name) => name.startsWith(uploadPrefix));
    await mkdir(dirPath, { recursive: true });
    await writeFile(join(dirPath, "README.md"), "# Demo\n");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_resource.zip" }))
      .mockResolvedValueOnce(okResponse({ root_uri: "viking://resources/resource-dir" }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000);
    await client.addResource({ pathOrUrl: dirPath });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect((fetchMock.mock.calls[0]![1] as RequestInit).body).toBeInstanceOf(FormData);
    expect(JSON.parse(String((fetchMock.mock.calls[1]![1] as RequestInit).body))).toMatchObject({
      temp_file_id: "upload_resource.zip",
      source_name: "resource-dir",
    });
    const afterDirs = (await readdir(tmpdir())).filter((name) => name.startsWith(uploadPrefix));
    expect(afterDirs).toEqual(beforeDirs);
  });

  it("addSkill uploads local SKILL.md file", async () => {
    const tempDir = await mkdtemp(join(tmpdir(), "ov-client-test-"));
    const filePath = join(tempDir, "SKILL.md");
    await writeFile(filePath, "---\nname: demo\ndescription: demo\n---\n\n# Demo\n");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_skill.md" }))
      .mockResolvedValueOnce(okResponse({ uri: "viking://agent/skills/demo", name: "demo" }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000);
    const result = await client.addSkill({ path: filePath, wait: true });

    expect(result.uri).toBe("viking://agent/skills/demo");
    expect(JSON.parse(String((fetchMock.mock.calls[1]![1] as RequestInit).body))).toMatchObject({
      temp_file_id: "upload_skill.md",
      wait: true,
    });
  });

  it("addSkill removes temporary zip directory after uploading a skill directory", async () => {
    const tempDir = await mkdtemp(join(tmpdir(), "ov-client-test-"));
    const dirPath = join(tempDir, "skill-dir");
    const uploadPrefix = "openviking-openclaw-upload-";
    const beforeDirs = (await readdir(tmpdir())).filter((name) => name.startsWith(uploadPrefix));
    await mkdir(dirPath, { recursive: true });
    await writeFile(join(dirPath, "SKILL.md"), "---\nname: demo\ndescription: demo\n---\n\n# Demo\n");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_skill.zip" }))
      .mockResolvedValueOnce(okResponse({ uri: "viking://agent/skills/demo", name: "demo" }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000);
    await client.addSkill({ path: dirPath, wait: true });

    expect(JSON.parse(String((fetchMock.mock.calls[1]![1] as RequestInit).body))).toMatchObject({
      temp_file_id: "upload_skill.zip",
      wait: true,
    });
    const afterDirs = (await readdir(tmpdir())).filter((name) => name.startsWith(uploadPrefix));
    expect(afterDirs).toEqual(beforeDirs);
  });

  it("addSkill posts raw skill data directly", async () => {
    const data = "---\nname: inline\ndescription: inline\n---\n\n# Inline\n";
    const fetchMock = vi.fn().mockResolvedValue(
      okResponse({ uri: "viking://agent/skills/inline", name: "inline" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000);
    await client.addSkill({ data });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String((fetchMock.mock.calls[0]![1] as RequestInit).body))).toMatchObject({
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
    const fetchMock = vi.fn().mockResolvedValue(
      okResponse({ uri: "viking://agent/skills/demo-tool", name: "demo-tool" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000);
    await client.addSkill({ data });

    expect(JSON.parse(String((fetchMock.mock.calls[0]![1] as RequestInit).body))).toMatchObject({
      data,
    });
  });

  it("surfaces OpenViking error responses", async () => {
    const fetchMock = vi.fn().mockResolvedValue(errorResponse("bad import"));
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5000);
    await expect(client.addResource({ pathOrUrl: "https://example.com/bad" })).rejects.toThrow(
      "OpenViking request failed [INVALID_ARGUMENT]: bad import",
    );
  });

  it("uses an extended request timeout for wait=true imports", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      setTimeout(() => {
        resolve(okResponse({ root_uri: "viking://resources/site", status: "success" }));
      }, 20_000);
    }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 15_000);
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
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      setTimeout(() => {
        resolve(okResponse({ root_uri: "viking://resources/site", status: "success" }));
      }, 20_000);
    }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 15_000);
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
    const fetchMock = vi.fn((url: string) => {
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
    vi.stubGlobal("fetch", fetchMock);

    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5_000);
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
