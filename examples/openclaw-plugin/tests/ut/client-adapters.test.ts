import { describe, expect, it, vi } from "vitest";

import { OpenVikingClient } from "../../client.js";
import type { HttpTransport } from "../../adapters/http-transport.js";
import type { ResourcePackager } from "../../adapters/resource-packager.js";

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

describe("OpenVikingClient adapter seams", () => {
  it("uses injected HTTP transport while preserving tenant, agent, body, and timeout headers", async () => {
    const transport = vi.fn<HttpTransport>().mockResolvedValue(okResponse({ status: "ok" }));
    const client = new OpenVikingClient(
      "http://127.0.0.1:1933",
      "sk-root",
      "agent-prefix",
      5_000,
      "acct-1",
      "user-1",
      undefined,
      { transport },
    );

    await client.healthCheck(12_000, "runtime-agent");

    expect(transport).toHaveBeenCalledTimes(1);
    const [url, init] = transport.mock.calls[0]!;
    const headers = new Headers(init.headers);
    expect(url).toBe("http://127.0.0.1:1933/health");
    expect(headers.get("X-API-Key")).toBe("sk-root");
    expect(headers.get("X-OpenViking-Account")).toBe("acct-1");
    expect(headers.get("X-OpenViking-User")).toBe("user-1");
    expect(headers.get("X-OpenViking-Actor-Peer")).toBe("runtime-agent");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("surfaces injected HTTP transport error payloads unchanged", async () => {
    const transport = vi.fn<HttpTransport>().mockResolvedValue(errorResponse("bad import"));
    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5_000, "", "", undefined, { transport });

    await expect(client.healthCheck()).rejects.toThrow("OpenViking request failed [INVALID_ARGUMENT]: bad import");
  });

  it("uses injected resource packager so remote URLs do not trigger local packaging", async () => {
    const transport = vi.fn<HttpTransport>().mockResolvedValue(okResponse({ root_uri: "viking://resources/site" }));
    const packager: ResourcePackager = {
      prepareResourceSource: vi.fn().mockResolvedValue({ kind: "remote", path: "https://example.com/docs" }),
      prepareLocalUploadSource: vi.fn(),
      createTempUploadBody: vi.fn(),
      cleanup: vi.fn(),
    };
    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5_000, "", "", undefined, {
      transport,
      resourcePackager: packager,
    });

    await client.addResource({ pathOrUrl: "https://example.com/docs", wait: true });

    expect(packager.prepareResourceSource).toHaveBeenCalledWith("https://example.com/docs");
    expect(packager.prepareLocalUploadSource).not.toHaveBeenCalled();
    expect(packager.cleanup).toHaveBeenCalledWith({ kind: "remote", path: "https://example.com/docs" });
    expect(transport).toHaveBeenCalledTimes(1);
    const [, init] = transport.mock.calls[0]!;
    expect(JSON.parse(String(init.body))).toMatchObject({
      path: "https://example.com/docs",
      wait: true,
    });
  });

  it("uses injected resource packager for local directories before temp upload", async () => {
    const uploadPath = "/fake-packager/resource-dir.zip";
    const uploadBody = new FormData();
    uploadBody.append("file", new Blob(["zip-bytes"]), "resource-dir.zip");
    const transport = vi
      .fn<HttpTransport>()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_resource.zip" }))
      .mockResolvedValueOnce(okResponse({ root_uri: "viking://resources/dir" }));
    const packaged = {
      kind: "upload" as const,
      uploadPath,
      sourceName: "resource-dir",
      cleanupPath: uploadPath,
    };
    const packager: ResourcePackager = {
      prepareResourceSource: vi.fn().mockResolvedValue(packaged),
      prepareLocalUploadSource: vi.fn(),
      createTempUploadBody: vi.fn().mockResolvedValue(uploadBody),
      cleanup: vi.fn(),
    };
    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5_000, "", "", undefined, {
      transport,
      resourcePackager: packager,
    });

    await client.addResource({ pathOrUrl: "/workspace/resource-dir" });

    expect(packager.prepareResourceSource).toHaveBeenCalledWith("/workspace/resource-dir");
    expect(packager.createTempUploadBody).toHaveBeenCalledWith(uploadPath);
    expect(packager.cleanup).toHaveBeenCalledWith(packaged);
    expect(transport.mock.calls[0]![0]).toBe("http://127.0.0.1:1933/api/v1/resources/temp_upload");
    expect(JSON.parse(String(transport.mock.calls[1]![1].body))).toMatchObject({
      temp_file_id: "upload_resource.zip",
      source_name: "resource-dir",
    });
  });

  it("uses injected clock and sleep for wait=true commit polling", async () => {
    let now = 0;
    const sleeps: number[] = [];
    const transport = vi
      .fn<HttpTransport>()
      .mockResolvedValueOnce(okResponse({ status: "pending", task_id: "task-1" }))
      .mockResolvedValueOnce(okResponse({ status: "running" }))
      .mockResolvedValueOnce(okResponse({ status: "completed", result: { memories_extracted: { user: 2 } } }));
    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5_000, "", "", undefined, {
      transport,
      now: () => now,
      sleep: async (ms) => {
        sleeps.push(ms);
        now += ms;
      },
    });

    const result = await client.commitSession("session-1", { wait: true, timeoutMs: 2_000, agentId: "runtime-agent" });

    expect(result).toMatchObject({
      status: "completed",
      task_id: "task-1",
      memories_extracted: { user: 2 },
    });
    expect(sleeps).toEqual([500, 500]);
    expect(transport.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:1933/api/v1/sessions/session-1/commit",
      "http://127.0.0.1:1933/api/v1/tasks/task-1",
      "http://127.0.0.1:1933/api/v1/tasks/task-1",
    ]);
  });

  it("uses injected clock deadline for wait=true commit polling timeout", async () => {
    let now = 0;
    const transport = vi
      .fn<HttpTransport>()
      .mockResolvedValueOnce(okResponse({ status: "pending", task_id: "task-timeout" }))
      .mockResolvedValue(okResponse({ status: "running" }));
    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5_000, "", "", undefined, {
      transport,
      now: () => now,
      sleep: async (ms) => {
        now += ms;
      },
    });

    const result = await client.commitSession("slow-session", { wait: true, timeoutMs: 1_000 });

    expect(result).toMatchObject({ status: "timeout", task_id: "task-timeout" });
    expect(transport.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:1933/api/v1/sessions/slow-session/commit",
      "http://127.0.0.1:1933/api/v1/tasks/task-timeout",
      "http://127.0.0.1:1933/api/v1/tasks/task-timeout",
    ]);
  });
});
