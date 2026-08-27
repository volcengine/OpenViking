import { describe, expect, it, vi } from "vitest";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  OpenVikingClient,
  OpenVikingError,
  normalizeURI,
} from "../src/index.js";
import type { AddResourceOptions } from "../src/index.js";

const ok = (result: unknown) =>
  new Response(JSON.stringify({ status: "ok", result }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

describe("OpenVikingClient", () => {
  it("does not expose a top-level resource parse mode option", () => {
    const options: AddResourceOptions = {
      // @ts-expect-error parse_mode is configured through args
      parseMode: "no_split",
    };
    expect((options as unknown as Record<string, unknown>).parseMode).toBe(
      "no_split",
    );
  });

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

  it("assembles context with dedicated options and rejects mode override", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        ok({ rendered: "<memory />", entries: [], stats: {} }),
      );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await expect(
      client.searchContext("continue refactor", {
        sessionId: "session-1",
        purpose: "coding",
        maxTokens: 3000,
        dedupTurns: 5,
      }),
    ).resolves.toMatchObject({ rendered: "<memory />" });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      query: "continue refactor",
      mode: "context",
      session_id: "session-1",
      purpose: "coding",
      max_tokens: 3000,
      dedup_turns: 5,
    });
    await expect(
      client.searchContext("query", { extra: { mode: "list" } }),
    ).rejects.toThrow(/mode/);
  });

  it("sends latest session message and retention fields", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.addMessage("session-1", {
      role: "assistant",
      content: "done",
      turnId: "turn-1",
      messageKind: "assistant_step",
      sourceMessageIds: ["user-1"],
    });
    await client.commitSession("session-1", {
      retentionMode: "turn_budget",
      keepRecentTurnCount: 3,
      retainedMessageTokenBudget: 12_000,
      minRawTailSteps: 1,
    });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      role: "assistant",
      content: "done",
      turn_id: "turn-1",
      message_kind: "assistant_step",
      source_message_ids: ["user-1"],
    });
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toEqual({
      retention_mode: "turn_budget",
      keep_recent_turn_count: 3,
      retained_message_token_budget: 12_000,
      min_raw_tail_steps: 1,
    });
  });

  it("supports legacy batch and commit session arguments", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    const telemetry = { trace_id: "trace-1" };

    await client.batchAddMessages(
      "session-1",
      [{ role: "user", content: "hello" }],
      telemetry,
    );
    await client.commitSession(
      "session-1",
      2,
      telemetry,
      ["team=platform"],
    );

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      messages: [{ role: "user", content: "hello" }],
      telemetry,
    });
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toEqual({
      keep_recent_count: 2,
      extraction_metadata: { event: { tags: ["team=platform"] } },
      telemetry,
    });
  });

  it("queries Agent Evolution trajectories and outcomes", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.listExperienceTrajectories("user/memories/experiences/a.md", {
      limit: 25,
      offset: 50,
      startDate: "2026-08-01",
      endDate: "2026-08-10",
    });
    await client.getExperienceOutcomes("user/memories/experiences/a.md", {
      startDate: "2026-08-01",
      endDate: "2026-08-10",
    });

    const first = new URL(String(fetcher.mock.calls[0]![0]));
    const second = new URL(String(fetcher.mock.calls[1]![0]));
    expect(first.pathname).toBe(
      "/api/v1/agent-evolution/experiences/trajectories",
    );
    expect(first.searchParams.get("experience_uri")).toBe(
      "viking://user/memories/experiences/a.md",
    );
    expect(first.searchParams.get("limit")).toBe("25");
    expect(first.searchParams.get("offset")).toBe("50");
    expect(first.searchParams.get("start_date")).toBe("2026-08-01");
    expect(first.searchParams.get("end_date")).toBe("2026-08-10");
    expect(second.pathname).toBe(
      "/api/v1/agent-evolution/experiences/outcomes",
    );
    expect(second.searchParams.get("start_date")).toBe("2026-08-01");
    expect(second.searchParams.get("end_date")).toBe("2026-08-10");
  });

  it("resolves and preflights OpenViking Assets with latest Git fields", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.resolveOpenVikingAssets("protocol: openviking-assets/1", {
      manifestLabel: "custom.yaml",
      extra: { future_flag: false },
    });
    await client.preflightOpenVikingAsset(
      "private-repo",
      "https://github.com/example/private.git",
      {
        branch: "main",
        commit: "0123456789abcdef",
        authConfig: { username: "oauth2", token: "secret" },
      },
    );

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      manifest_yaml: "protocol: openviking-assets/1",
      manifest_label: "custom.yaml",
      future_flag: false,
    });
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toEqual({
      name: "private-repo",
      connector: "git",
      repo_url: "https://github.com/example/private.git",
      branch: "main",
      commit: "0123456789abcdef",
      auth_config: { username: "oauth2", token: "secret" },
    });
  });

  it("omits unset retrieval options and uses server defaults", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ resources: [] }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.search("hello");

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      query: "hello",
    });
  });

  it("preserves explicit zero and empty retrieval options", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ resources: [] }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.find("hello", { limit: 0, tags: [], level: [] });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      query: "hello",
      limit: 0,
      tags: [],
      level: [],
    });
  });

  it("forwards readContent for ranked retrieval", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ resources: [] }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.find("hello", { readContent: true });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      query: "hello",
      read_content: true,
    });
  });

  it("sends dry_run for prune_orphans reindex requests", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ status: "completed" }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.reindex("resources", {
      mode: "prune_orphans",
      wait: true,
      dryRun: true,
      recursive: false,
    });

    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe("https://example.com/api/v1/content/reindex");
    expect(JSON.parse(String(init?.body))).toEqual({
      uri: "viking://resources",
      mode: "prune_orphans",
      wait: true,
      dry_run: true,
      recursive: false,
    });
  });

  it("sends explicit empty tags for reindex requests", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ status: "completed" }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.reindex("resources", {
      tags: [],
      tagMode: "replace",
    });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toMatchObject({
      tags: [],
      tag_mode: "replace",
    });
  });

  it("sends reindex extra fields and rejects official-field overrides", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ status: "completed" }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.reindex("resources", {
      extra: { future_flag: false },
    });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toMatchObject({
      future_flag: false,
    });
    expect(() =>
      client.reindex("resources", {
        extra: { tags: ["team=search"] },
      }),
    ).toThrow("extra cannot override tags");
  });

  it("sends processing_mode for addResource requests", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.addResource("https://example.com/guide.md", {
      to: "viking://resources/guide",
      processingMode: "vectors_only",
      wait: true,
    });

    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe("https://example.com/api/v1/resources");
    expect(JSON.parse(String(init?.body))).toMatchObject({
      path: "https://example.com/guide.md",
      to: "viking://resources/guide",
      processing_mode: "vectors_only",
      wait: true,
    });
  });

  it("sends processing_mode for write requests", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.write("resources/demo.md", "updated", {
      processingMode: "vectors_only",
      wait: true,
    });

    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe("https://example.com/api/v1/content/write");
    expect(JSON.parse(String(init?.body))).toEqual({
      uri: "viking://resources/demo.md",
      content: "updated",
      processing_mode: "vectors_only",
      wait: true,
    });
  });

  it("supports batch write, byte download, and resource extra", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementationOnce(async () => ok({}))
      .mockImplementationOnce(
        async () =>
          new Response(new Uint8Array([1, 2, 3]), {
            status: 200,
            headers: { "Content-Type": "application/octet-stream" },
          }),
      )
      .mockImplementationOnce(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.batchWrite(
      "resources/project",
      [
        {
          uri: "resources/project/a.txt",
          content: "hello",
          mode: "upsert",
        },
      ],
      { extra: { future_flag: 0 } },
    );
    await expect(
      client.downloadBytes("resources/project/a.txt"),
    ).resolves.toEqual(new Uint8Array([1, 2, 3]));
    await client.addResource("https://example.com/a.md", {
      createParent: false,
      extra: { future_flag: false },
    });

    const batchWriteBody = JSON.parse(String(fetcher.mock.calls[0]![1]?.body));
    expect(batchWriteBody).toMatchObject({
      root_uri: "viking://resources/project",
      future_flag: 0,
    });
    expect(batchWriteBody.operations).toEqual([
      {
        uri: "viking://resources/project/a.txt",
        content: "hello",
        mode: "upsert",
      },
    ]);
    expect(
      new URL(String(fetcher.mock.calls[1]![0])).searchParams.get("uri"),
    ).toBe("viking://resources/project/a.txt");
    expect(JSON.parse(String(fetcher.mock.calls[2]![1]?.body))).toMatchObject({
      create_parent: false,
      future_flag: false,
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

  it("passes directory list ordering and tree depth to the server", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok([]));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.list("viking://session", {
      nodeLimit: 200,
      sortBy: "mtime",
      sortOrder: "desc",
    });
    await client.tree("viking://resources/docs", { levelLimit: 2 });
    await client.tree("viking://resources/docs", { levelLimit: 0 });
    await client.tree("viking://resources/docs");

    const listUrl = new URL(String(fetcher.mock.calls[0]![0]));
    expect(listUrl.searchParams.get("node_limit")).toBe("200");
    expect(listUrl.searchParams.get("sort_by")).toBe("mtime");
    expect(listUrl.searchParams.get("sort_order")).toBe("desc");
    const treeLimits = fetcher.mock.calls
      .slice(1)
      .map((call) => new URL(String(call[0])).searchParams.get("level_limit"));
    expect(treeLimits).toEqual(["2", "0", "3"]);
  });

  it("sends addResource tags and tagMode to the server", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ root_uri: "viking://resources/demo" }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.addResource("https://example.com/demo.md", {
      tags: ["team=search"],
      tagMode: "append",
    });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toMatchObject({
      path: "https://example.com/demo.md",
      tags: ["team=search"],
      tag_mode: "append",
    });
  });

  it("forwards setTags extra and rejects official-field overrides", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({ updated: 1 }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.setTags("resources/demo.md", ["team=search"], {
      extra: { future_flag: false },
    });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      uri: "viking://resources/demo.md",
      tags: ["team=search"],
      mode: "replace",
      recursive: false,
      future_flag: false,
    });
    expect(() =>
      client.setTags("resources/demo.md", ["team=search"], {
        extra: { uri: "viking://other" },
      }),
    ).toThrow("extra cannot override uri");
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

  it("sends event memory tag configuration for session APIs", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    const memoryExtractionConfig = {
      events: { tags: ["team=search", "channel=web"] },
    };

    await client.createSession({ sessionId: "tagged", memoryExtractionConfig });
    await client.updateSessionConfig("tagged", {
      memoryExtractionConfig,
      autoCommitPolicy: { message_count_threshold: 25 },
    });
    await client.commitSession("tagged", {
      keepRecentCount: 0,
      eventTags: [],
    });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      session_id: "tagged",
      memory_extraction_config: memoryExtractionConfig,
    });
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toEqual({
      memory_extraction_config: memoryExtractionConfig,
      auto_commit_policy: { message_count_threshold: 25 },
    });
    expect(JSON.parse(String(fetcher.mock.calls[2]![1]?.body))).toEqual({
      keep_recent_count: 0,
      extraction_metadata: { event: { tags: [] } },
    });

    await client.updateSessionConfig("tagged", { autoCommitPolicy: null });
    expect(JSON.parse(String(fetcher.mock.calls[3]![1]?.body))).toEqual({
      auto_commit_policy: null,
    });

    await client.createSession({ autoCommitPolicy: null });
    expect(JSON.parse(String(fetcher.mock.calls[4]![1]?.body))).toEqual({
      auto_commit_policy: null,
    });
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

  it("serializes resource parse mode through args", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.addResource("https://example.com/default.md");
    await client.addResource("https://example.com/raw.pdf", {
      args: { parse_mode: "no_split" },
    });

    const defaultBody = JSON.parse(String(fetcher.mock.calls[0]![1]?.body));
    const noSplitBody = JSON.parse(String(fetcher.mock.calls[1]![1]?.body));
    expect(defaultBody).not.toHaveProperty("parse_mode");
    expect(noSplitBody).not.toHaveProperty("parse_mode");
    expect(noSplitBody.args).toEqual({ parse_mode: "no_split" });
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
    ).rejects.toMatchObject({
      code: "ABORTED",
      message: "Request was aborted by the caller",
    });
  });

  it("maps OpenVikingError abort reasons to caller cancellation", async () => {
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
    controller.abort(new OpenVikingError("cancelled", { code: "UNAVAILABLE" }));

    await expect(
      client.backupOVPack("backup", false, { signal: controller.signal }),
    ).rejects.toMatchObject({
      code: "ABORTED",
      message: "Request was aborted by the caller",
    });
  });

  it("keeps in-flight caller cancellation distinct from timeouts", async () => {
    let markStarted: (() => void) | undefined;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      async (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          markStarted?.();
          init?.signal?.addEventListener("abort", () =>
            reject(init.signal?.reason),
          );
        }),
    );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
      timeout: 10_000,
    });
    const controller = new AbortController();

    const request = client.backupOVPack("backup", false, {
      signal: controller.signal,
    });
    await started;
    controller.abort(new Error("cancelled"));

    await expect(request).rejects.toMatchObject({ code: "ABORTED" });
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

  it("maps snapshot and health APIs to the Python contracts", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(ok({ oid: "commit" }))
      .mockResolvedValueOnce(ok({ is_healthy: true }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.gitCommit({ message: "snapshot" });
    await expect(client.isHealthy()).resolves.toBe(true);

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      message: "snapshot",
      branch: "main",
    });
  });

  it("supports snapshot restore, binary show, log, diff and ignore operations", async () => {
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
      .mockResolvedValueOnce(ok([{ oid: "commit-1" }]))
      .mockResolvedValueOnce(
        ok({
          path: "viking://resources/a",
          from_commit: "old",
          to_commit: "new",
          change_type: "modified",
          diff_text: "@@ -1 +1 @@\n-old\n+new\n",
        }),
      )
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
    await expect(
      client.gitLog("main", 20, [
        "viking://resources/a",
        "viking://resources/docs",
      ]),
    ).resolves.toEqual([{ oid: "commit-1" }]);
    await expect(client.gitLog("main", 20, [])).resolves.toEqual([
      { oid: "commit-1" },
    ]);
    await expect(
      client.gitDiff("viking://resources/a", "new", "old"),
    ).resolves.toMatchObject({ change_type: "modified" });
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
    const logUrl = new URL(String(fetcher.mock.calls[2]![0]));
    expect(logUrl.searchParams.get("limit")).toBe("20");
    expect(logUrl.searchParams.getAll("paths")).toEqual([
      "viking://resources/a",
      "viking://resources/docs",
    ]);
    const unfilteredLogUrl = new URL(String(fetcher.mock.calls[3]![0]));
    expect(unfilteredLogUrl.searchParams.get("limit")).toBe("20");
    expect(unfilteredLogUrl.searchParams.getAll("paths")).toEqual([]);
    const diffUrl = new URL(String(fetcher.mock.calls[4]![0]));
    expect(diffUrl.pathname).toBe("/api/v1/snapshot/diff");
    expect(diffUrl.searchParams.get("path")).toBe("viking://resources/a");
    expect(diffUrl.searchParams.get("from")).toBe("old");
    expect(diffUrl.searchParams.get("to")).toBe("new");
    expect(JSON.parse(String(fetcher.mock.calls[6]![1]?.body))).toEqual({
      content: "*.log\n",
    });
  });
});
