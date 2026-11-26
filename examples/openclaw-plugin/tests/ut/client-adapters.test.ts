import { access, mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { strFromU8, unzipSync } from "fflate";
import { describe, expect, it, vi } from "vitest";

import { OpenVikingClient } from "../../client.js";
import type { HttpTransport } from "../../adapters/http-transport.js";
import { defaultResourcePackager } from "../../adapters/resource-packager.js";

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

  it("passes remote sources directly and uploads an actual archive for local directories", async () => {
    const folder = await mkdtemp(join(tmpdir(), "ov-client-resource-"));
    const remote = "https://example.com/docs";
    let archivePath: string | undefined;
    const resourcePackager = {
      ...defaultResourcePackager,
      async prepareResourceSource(path: string) {
        const source = await defaultResourcePackager.prepareResourceSource(path);
        if (source.kind === "upload") archivePath = source.cleanupPath;
        return source;
      },
    };
    const transport = vi.fn<HttpTransport>(async (url, init) => {
      const endpoint = new URL(url).pathname;
      if (endpoint === "/api/v1/resources/temp_upload") {
        expect(init.body).toBeInstanceOf(FormData);
        const file = (init.body as FormData).get("file");
        expect(file).toBeInstanceOf(Blob);
        const entries = unzipSync(new Uint8Array(await (file as Blob).arrayBuffer()));
        expect(Object.keys(entries)).toEqual(["nested/note.md"]);
        expect(strFromU8(entries["nested/note.md"]!)).toBe("archive content");
        return okResponse({ temp_file_id: "uploaded-draft" });
      }
      expect(endpoint).toBe("/api/v1/resources");
      return okResponse({ root_uri: "viking://resources/docs" });
    });
    const client = new OpenVikingClient("http://127.0.0.1:1933", "", "agent", 5_000, "", "", undefined, {
      transport,
      resourcePackager,
    });

    try {
      await client.addResource({ pathOrUrl: remote });
      expect(transport).toHaveBeenCalledTimes(1);
      expect(JSON.parse(String(transport.mock.calls[0]![1].body))).toMatchObject({ path: remote });

      await mkdir(join(folder, "nested"));
      await writeFile(join(folder, "nested", "note.md"), "archive content");
      await client.addResource({ pathOrUrl: folder });
      expect(transport).toHaveBeenCalledTimes(3);
      const ingest = JSON.parse(String(transport.mock.calls[2]![1].body));
      expect(ingest).toHaveProperty("temp_file_id", "uploaded-draft");
      expect(ingest).not.toHaveProperty("path");
      expect(archivePath).toBeDefined();
      await expect(access(archivePath!)).rejects.toMatchObject({ code: "ENOENT" });
    } finally {
      await rm(folder, { recursive: true, force: true });
    }
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

    const result = await client.commitSession("session-1", { wait: true, agentId: "runtime-agent" });

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
        now += 150_000;
      },
    });

    const result = await client.commitSession("slow-session", { wait: true });

    expect(result).toMatchObject({ status: "timeout", task_id: "task-timeout" });
    expect(transport.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:1933/api/v1/sessions/slow-session/commit",
      "http://127.0.0.1:1933/api/v1/tasks/task-timeout",
      "http://127.0.0.1:1933/api/v1/tasks/task-timeout",
    ]);
  });
});
