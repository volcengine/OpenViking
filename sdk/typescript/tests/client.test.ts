import { describe, expect, it, vi } from "vitest";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
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

  it("uses the Python/Go empty default retrieval target", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ resources: [] }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.search("hello");

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toMatchObject({
      target_uri: "",
    });
  });

  it("sends dry_run for prune_orphans reindex requests", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({ status: "completed" }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.reindex("resources", {
      mode: "prune_orphans",
      wait: true,
      dryRun: true,
    });

    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe("https://example.com/api/v1/content/reindex");
    expect(JSON.parse(String(init?.body))).toEqual({
      uri: "viking://resources",
      mode: "prune_orphans",
      wait: true,
      dry_run: true,
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

  it("passes directory list ordering to the server", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok([]));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.list("viking://session", {
      nodeLimit: 200,
      sortBy: "mtime",
      sortOrder: "desc",
    });

    const url = new URL(String(fetcher.mock.calls[0]![0]));
    expect(url.searchParams.get("node_limit")).toBe("200");
    expect(url.searchParams.get("sort_by")).toBe("mtime");
    expect(url.searchParams.get("sort_order")).toBe("desc");
  });

  it("converts an existing Node.js image path to a data URI", async () => {
    const directory = await mkdtemp(join(tmpdir(), "openviking-sdk-image-"));
    const path = join(directory, "photo.png");
    await writeFile(path, new Uint8Array([137, 80, 78, 71]));
    try {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
      });

      await client.find("", { image: path });

      const body = JSON.parse(String(fetcher.mock.calls[0]![1]?.body));
      expect(body.image_url).toBe("data:image/png;base64,iVBORw==");
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("uses the MIME type for a local BMP image", async () => {
    const directory = await mkdtemp(join(tmpdir(), "openviking-sdk-image-"));
    const path = join(directory, "photo.bmp");
    await writeFile(path, new Uint8Array([66, 77]));
    try {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
      });

      await client.find("", { image: path });

      const body = JSON.parse(String(fetcher.mock.calls[0]![1]?.body));
      expect(body.image_url).toBe("data:image/bmp;base64,Qk0=");
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it.each([
    `data:image/png;base64,${"A".repeat(8192)}`,
    "http://example.com/photo.png",
    "https://example.com/photo.png",
    "viking://resources/photo.png",
  ])(
    "passes image references through without filesystem access",
    async (image) => {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
      });

      await client.search("", { image });

      const body = JSON.parse(String(fetcher.mock.calls[0]![1]?.body));
      expect(body.image_url).toBe(image);
    },
  );

  it("normalizes empty parts consistently in batch messages", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.batchAddMessages("session", [
      { role: "user", content: "hello", parts: [] },
    ]);

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      messages: [{ role: "user", content: "hello" }],
    });
    expect(() =>
      client.batchAddMessages("session", [{ role: "user", parts: [] }]),
    ).toThrow("each message requires content or parts");
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
    const directory = await mkdtemp(join(tmpdir(), "openviking-sdk-error-"));
    const path = join(directory, "resource.md");
    await writeFile(path, "hello");
    try {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValue(new Response("gateway failure", { status: 502 }));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
      });
      await expect(client.addResource(path)).rejects.toMatchObject({
        statusCode: 502,
      });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
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
        uploadMode: "shared",
      });
      await client.addResource(path);
      expect(String(fetcher.mock.calls[0]![0])).toBe(
        "https://example.com/api/v1/resources/temp_upload",
      );
      const form = fetcher.mock.calls[0]![1]?.body as FormData;
      expect((form.get("file") as File).name).toBe("resource.md");
      expect(form.get("upload_mode")).toBe("shared");
      expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toMatchObject(
        { temp_file_id: "temp-local", source_name: "resource.md" },
      );
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("streams OVPack exports to a normalized local file", async () => {
    const directory = await mkdtemp(join(tmpdir(), "openviking-sdk-pack-"));
    try {
      await writeFile(join(directory, "docs.ovpack"), "old-backup");
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValue(new Response(new Uint8Array([1, 2, 3])));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
        profile: true,
      });

      const output = await client.exportOVPack(
        "viking://resources/docs",
        directory,
        true,
      );

      expect(output).toBe(join(directory, "docs.ovpack"));
      expect(await readFile(output)).toEqual(Buffer.from([1, 2, 3]));
      expect(await readdir(directory)).toEqual(["docs.ovpack"]);
      const [url, init] = fetcher.mock.calls[0]!;
      expect(new URL(String(url)).searchParams.get("profile")).toBe("1");
      expect(JSON.parse(String(init?.body))).toEqual({
        uri: "viking://resources/docs",
        include_vectors: true,
      });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it.each([true, false])(
    "preserves the final OVPack when a download stream fails",
    async (existingOutput) => {
      const directory = await mkdtemp(join(tmpdir(), "openviking-sdk-pack-"));
      const output = join(directory, "backup.ovpack");
      if (existingOutput) await writeFile(output, "known-good-backup");
      let pulls = 0;
      try {
        const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
          new Response(
            new ReadableStream({
              pull(controller) {
                if (pulls++ === 0)
                  controller.enqueue(new TextEncoder().encode("partial"));
                else controller.error(new Error("connection reset"));
              },
            }),
            {
              status: 200,
              headers: { "Content-Type": "application/octet-stream" },
            },
          ),
        );
        const client = new OpenVikingClient({
          baseUrl: "https://example.com",
          fetch: fetcher,
        });

        await expect(client.backupOVPack(output)).rejects.toMatchObject({
          code: "UNAVAILABLE",
        });

        if (existingOutput)
          expect(await readFile(output, "utf8")).toBe("known-good-backup");
        else
          await expect(readFile(output)).rejects.toMatchObject({
            code: "ENOENT",
          });
        expect(await readdir(directory)).toEqual(
          existingOutput ? ["backup.ovpack"] : [],
        );
      } finally {
        await rm(directory, { recursive: true, force: true });
      }
    },
  );

  it("maps structured OVPack download failures", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "error",
          error: { code: "FORBIDDEN", message: "denied" },
        }),
        { status: 403 },
      ),
    );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await expect(client.backupOVPack("backup")).rejects.toMatchObject({
      code: "FORBIDDEN",
      statusCode: 403,
    });
  });

  it("maps non-JSON OVPack download failures", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("gateway failure", { status: 502 }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await expect(client.backupOVPack("backup")).rejects.toMatchObject({
      statusCode: 502,
    });
  });

  it("cancels OVPack downloads on timeout", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      async (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(init.signal?.reason),
          );
        }),
    );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
      timeout: 5,
    });

    await expect(client.backupOVPack("backup")).rejects.toMatchObject({
      code: "DEADLINE_EXCEEDED",
    });
  });

  it("forwards caller cancellation to OVPack downloads", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (_input, init) => {
        if (init?.signal?.aborted) throw init.signal.reason;
        return new Response(new Uint8Array([1]));
      });
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    const controller = new AbortController();
    controller.abort(new Error("cancelled"));

    await expect(
      client.backupOVPack("backup", false, { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "DEADLINE_EXCEEDED" });
  });

  it("rejects directories before importing or restoring OVPack files", async () => {
    const directory = await mkdtemp(join(tmpdir(), "openviking-sdk-pack-dir-"));
    try {
      const fetcher = vi.fn<typeof fetch>();
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
      });

      await expect(
        client.importOVPack(directory, "viking://resources"),
      ).rejects.toThrow("expected an OVPack file");
      await expect(client.restoreOVPack(directory)).rejects.toThrow(
        "expected an OVPack file",
      );
      expect(fetcher).not.toHaveBeenCalled();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("maps relation and snapshot APIs to the Python contracts", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(ok([]))
      .mockResolvedValueOnce(ok({}))
      .mockResolvedValueOnce(ok({}))
      .mockResolvedValueOnce(ok({ oid: "commit" }))
      .mockResolvedValueOnce(ok({ is_healthy: true }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.relations("resources/a");
    await client.link("resources/a", ["resources/b"]);
    await client.unlink("resources/a", "resources/b");
    await client.gitCommit({ message: "snapshot" });
    await expect(client.isHealthy()).resolves.toBe(true);

    expect(String(fetcher.mock.calls[0]![0])).toContain(
      "uri=viking%3A%2F%2Fresources%2Fa",
    );
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toMatchObject({
      from_uri: "viking://resources/a",
      to_uris: ["viking://resources/b"],
    });
    expect(JSON.parse(String(fetcher.mock.calls[3]![1]?.body))).toEqual({
      message: "snapshot",
      branch: "main",
    });
  });

  it("supports snapshot restore, binary show, log and ignore operations", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(ok({ oid: "restored" }))
      .mockResolvedValueOnce(
        new Response(new Uint8Array([4, 5]), {
          headers: {
            "Content-Type": "application/octet-stream",
            "X-Snapshot-Oid": "blob-1",
            "X-Snapshot-Size": "2",
          },
        }),
      )
      .mockResolvedValueOnce(ok([{ oid: "commit-1" }]))
      .mockResolvedValueOnce(ok("*.tmp\n"))
      .mockResolvedValueOnce(ok(null))
      .mockResolvedValueOnce(ok(null));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.gitRestore({ sourceCommit: "old", dryRun: true });
    await expect(
      client.gitShow("main", "viking://resources/a"),
    ).resolves.toEqual({
      oid: "blob-1",
      size: 2,
      bytes: new Uint8Array([4, 5]),
    });
    await expect(client.gitLog()).resolves.toEqual([{ oid: "commit-1" }]);
    await expect(client.gitGetIgnore()).resolves.toBe("*.tmp\n");
    await client.gitSetIgnore("*.log\n");
    await client.gitDeleteIgnore();

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      source_commit: "old",
      branch: "main",
      dry_run: true,
    });
    expect(
      new URL(String(fetcher.mock.calls[1]![0])).searchParams.get("path"),
    ).toBe("viking://resources/a");
    expect(JSON.parse(String(fetcher.mock.calls[4]![1]?.body))).toEqual({
      content: "*.log\n",
    });
  });
});
