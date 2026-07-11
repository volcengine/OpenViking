import { describe, expect, it, vi } from "vitest";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  OpenVikingClient,
  OpenVikingError,
  normalizeURI,
} from "../src/index.js";

const ok = (result: unknown) =>
  new Response(JSON.stringify({ status: "ok", result }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

describe("OpenVikingClient", () => {
  it("normalizes URIs", () => {
    expect(normalizeURI("resources/docs")).toBe("viking://resources/docs");
    expect(normalizeURI("viking://resources/docs")).toBe(
      "viking://resources/docs",
    );
  });

  it("sends identity headers and the Python/Go compatible search body", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ resources: [] }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com/",
      apiKey: "key",
      account: "acme",
      user: "alice",
      actorPeerId: "peer",
      fetch: fetcher,
    });
    await client.find("hello", { targetUri: "viking://resources", limit: 5 });
    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe("https://example.com/api/v1/search/find");
    expect(new Headers(init?.headers).get("X-OpenViking-Actor-Peer")).toBe(
      "peer",
    );
    expect(JSON.parse(String(init?.body))).toMatchObject({
      query: "hello",
      target_uri: "viking://resources",
      limit: 5,
    });
  });

  it("maps response envelopes to typed errors", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "error",
          error: { code: "NOT_FOUND", message: "missing" },
        }),
        { status: 404 },
      ),
    );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    await expect(client.stat("missing")).rejects.toMatchObject({
      code: "NOT_FOUND",
      statusCode: 404,
    } satisfies Partial<OpenVikingError>);
  });

  it("uses the raw health contract", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
      );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    await expect(client.health()).resolves.toBe(true);
    expect(String(fetcher.mock.calls[0]![0])).toBe(
      "https://example.com/health",
    );
  });

  it("uploads blobs through the shared resource upload endpoint", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(ok({ temp_file_id: "temp-1" }))
      .mockResolvedValueOnce(ok({ task_id: "task-1" }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    await client.addResource(new Blob(["hello"], { type: "text/plain" }));
    expect(String(fetcher.mock.calls[0]![0])).toBe(
      "https://example.com/api/v1/resources/temp_upload",
    );
    expect(fetcher.mock.calls[0]![1]?.body).toBeInstanceOf(FormData);
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toMatchObject({
      temp_file_id: "temp-1",
    });
  });

  it("preserves browser file names and sends the configured upload mode", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(ok({ temp_file_id: "temp-file" }))
      .mockResolvedValueOnce(ok({ task_id: "task-file" }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
      uploadMode: "proxy",
    });
    const file = new Blob(["# guide"], { type: "text/markdown" }) as Blob & {
      name: string;
    };
    Object.defineProperty(file, "name", { value: "guide.md" });

    await client.addResource(file);

    const form = fetcher.mock.calls[0]![1]?.body as FormData;
    expect((form.get("file") as File).name).toBe("guide.md");
    expect(form.get("upload_mode")).toBe("proxy");
  });

  it("maps typed watch options to the server contract", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.updateWatch(
      { taskId: "task-1" },
      { watchInterval: 30, isActive: false, reason: "pause" },
    );

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      watch_interval: 30,
      is_active: false,
      reason: "pause",
    });
  });

  it("uses Go SDK defaults for skill details and returns null for missing tasks", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(ok({ name: "demo" }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "error",
            error: { code: "NOT_FOUND", message: "missing" },
          }),
          { status: 404 },
        ),
      );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.getSkill("demo");
    await expect(client.getTask("missing")).resolves.toBeNull();

    const skillUrl = new URL(String(fetcher.mock.calls[0]![0]));
    expect(skillUrl.searchParams.get("include_files")).toBe("true");
    expect(skillUrl.searchParams.get("include_source")).toBe("false");
  });

  it("preserves empty content in write and message requests", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    await client.write("resources/empty.md", "");
    await client.addMessage("session", { role: "assistant", content: "" });
    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toMatchObject({
      content: "",
    });
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toMatchObject({
      content: "",
    });
  });

  it("maps non-JSON upload failures to OpenVikingError", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("gateway failure", { status: 502 }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    await expect(client.addResource(new Blob(["hello"]))).rejects.toMatchObject(
      { statusCode: 502 },
    );
  });

  it("uploads an existing Node.js local file instead of sending its path to the server", async () => {
    const directory = await mkdtemp(join(tmpdir(), "openviking-sdk-"));
    const path = join(directory, "resource.md");
    await writeFile(path, "local content");
    try {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(ok({ temp_file_id: "temp-local" }))
        .mockResolvedValueOnce(ok({}));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
      });
      await client.addResource(path);
      expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toMatchObject(
        { temp_file_id: "temp-local", source_name: "resource.md" },
      );
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
