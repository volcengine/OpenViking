// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, expect, it, vi } from "vitest";
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { strFromU8, unzipSync } from "fflate";
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
      headers: { "X-Tenant": "test-tenant" },
      profile: true,
      fetch: fetcher,
    });
    await client.find("hello", {
      targetUri: "viking://resources",
      limit: 5,
      eventsTimeDecayProtection: "2d",
    });
    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe(
      "https://example.com/api/v1/search/find?profile=1",
    );
    expect(Object.fromEntries(new Headers(init?.headers))).toMatchObject({
      "x-api-key": "key",
      "x-openviking-account": "acme",
      "x-openviking-user": "alice",
      "x-openviking-actor-peer": "peer",
      "x-tenant": "test-tenant",
    });
    expect(JSON.parse(String(init?.body))).toMatchObject({
      query: "hello",
      target_uri: "viking://resources",
      limit: 5,
      events_time_decay_protection: "2d",
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
        searchType: "keywords",
        purpose: "coding",
        maxTokens: 3000,
        dedupTurns: 5,
        eventsTimeDecayProtection: "2d",
      }),
    ).resolves.toMatchObject({ rendered: "<memory />" });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      query: "continue refactor",
      mode: "context",
      search_type: "keywords",
      session_id: "session-1",
      purpose: "coding",
      max_tokens: 3000,
      dedup_turns: 5,
      events_time_decay_protection: "2d",
    });
    await expect(
      client.searchContext("query", { extra: { mode: "list" } }),
    ).rejects.toThrow(/mode/);
  });

  it("keeps legacy session arguments and validates batch messages", async () => {
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
    await client.commitSession("session-1", 2, telemetry, ["team=platform"]);

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      messages: [{ role: "user", content: "hello" }],
      telemetry,
    });
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toEqual({
      keep_recent_count: 2,
      extraction_metadata: { event: { tags: ["team=platform"] } },
      telemetry,
    });
    await client.batchAddMessages("session", [
      { role: "user", content: "hello", parts: [] },
    ]);
    expect(JSON.parse(String(fetcher.mock.lastCall![1]?.body))).toEqual({
      messages: [{ role: "user", content: "hello" }],
    });
    expect(() =>
      client.batchAddMessages("session", [{ role: "user", parts: [] }]),
    ).toThrow("each message requires content or parts");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("preserves retrieval defaults and explicitly supplied options", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({ resources: [] }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.search("hello");
    await client.search("OAuth token", { searchType: "keywords" });
    await client.find("hello", {
      limit: 3,
      tags: [],
      level: [0],
      readContent: true,
    });

    expect(
      fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body))),
    ).toEqual([
      { query: "hello" },
      { query: "OAuth token", search_type: "keywords" },
      { query: "hello", limit: 3, tags: [], level: [0], read_content: true },
    ]);
  });

  it("preserves explicit reindex options and rejects conflicting extra fields", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ status: "completed" }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.reindex("resources", {
      mode: "vectors_only",
      wait: true,
      force: true,
      recursive: false,
      tags: [],
      tagMode: "replace",
      extra: { future_flag: false },
    });
    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe("https://example.com/api/v1/content/reindex");
    expect(JSON.parse(String(init?.body))).toEqual({
      uri: "viking://resources",
      mode: "vectors_only",
      wait: true,
      force: true,
      recursive: false,
      tags: [],
      tag_mode: "replace",
      future_flag: false,
    });

    expect(() =>
      client.reindex("resources", {
        extra: { tags: ["team=search"] },
      }),
    ).toThrow("extra cannot override tags");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("keeps resource ingestion options and omits unset args", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () =>
        ok({ root_uri: "viking://resources/guide" }),
      );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    const source = "https://example.com/guide.pdf";

    await client.addResource(source);
    expect(
      JSON.parse(String(fetcher.mock.calls[0]![1]?.body)),
    ).not.toHaveProperty("args");
    const result = await client.addResource(source, {
      to: "viking://resources/guide",
      wait: true,
      processingMode: "vectors_only",
      tags: ["team=search"],
      tagMode: "append",
      args: { parse_mode: "no_split" },
      createParent: false,
      extra: { future_flag: false },
    });

    expect(result.root_uri).toBe("viking://resources/guide");
    const [url, init] = fetcher.mock.calls[1]!;
    expect(String(url)).toBe("https://example.com/api/v1/resources");
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({
      path: source,
      to: "viking://resources/guide",
      wait: true,
      processing_mode: "vectors_only",
      tags: ["team=search"],
      tag_mode: "append",
      args: { parse_mode: "no_split" },
      create_parent: false,
      future_flag: false,
    });
    expect(body).not.toHaveProperty("parse_mode");
  });

  it("preserves empty content and snapshot conflict resolution in a write", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    await client.write("resources/demo.md", "", {
      resolveConflicts: true,
      useSnapshot: true,
    });

    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({
      uri: "viking://resources/demo.md",
      content: "",
      resolve_conflicts: true,
      use_snapshot: true,
    });
  });

  it("supports batch write and byte download", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementationOnce(async () => ok({}))
      .mockImplementationOnce(
        async () =>
          new Response(new Uint8Array([1, 2, 3]), {
            status: 200,
            headers: { "Content-Type": "application/octet-stream" },
          }),
      );
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
          expectedSha256: "a".repeat(64),
          tags: ["team=search"],
          tagMode: "append",
          acl: {
            acl_mode: "restricted",
            entries: [{ principal: "group:engineering", level: "write" }],
          },
        },
      ],
      {
        processingMode: "vectors_only",
        skipConflicts: true,
        extra: { future_flag: 0 },
      },
    );
    await expect(
      client.downloadBytes("resources/project/a.txt"),
    ).resolves.toEqual(new Uint8Array([1, 2, 3]));

    const batchWriteBody = JSON.parse(String(fetcher.mock.calls[0]![1]?.body));
    expect(batchWriteBody).toMatchObject({
      root_uri: "viking://resources/project",
      future_flag: 0,
      skip_conflicts: true,
    });
    expect(batchWriteBody.operations).toEqual([
      {
        uri: "viking://resources/project/a.txt",
        content: "hello",
        mode: "upsert",
        expected_sha256: "a".repeat(64),
        tags: ["team=search"],
        tag_mode: "append",
        acl: {
          acl_mode: "restricted",
          entries: [{ principal: "group:engineering", level: "write" }],
        },
      },
    ]);
    expect(batchWriteBody.processing_mode).toBe("vectors_only");
    expect(batchWriteBody.defer_processing).toBeUndefined();
    expect(
      new URL(String(fetcher.mock.calls[1]![0])).searchParams.get("uri"),
    ).toBe("viking://resources/project/a.txt");
  });

  it("preserves API errors and identifies transport failures", async () => {
    const unavailable = new TypeError("connection lost");
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "error",
            error: {
              code: "NOT_FOUND",
              message: "missing",
              details: { resource: "viking://resources/missing" },
            },
          }),
          { status: 404 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "error",
            error: { code: "PROCESSING_ERROR", message: "operation failed" },
          }),
          { status: 200 },
        ),
      )
      .mockRejectedValueOnce(unavailable);
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    const missing = client.stat("resources/missing");
    await expect(missing).rejects.toBeInstanceOf(OpenVikingError);
    await expect(missing).rejects.toMatchObject({
      code: "NOT_FOUND",
      statusCode: 404,
      message: "missing",
      details: { resource: "viking://resources/missing" },
    });
    await expect(client.stat("resources/demo")).rejects.toMatchObject({
      code: "PROCESSING_ERROR",
      statusCode: 200,
      message: "operation failed",
    });
    await expect(client.stat("resources/demo")).rejects.toMatchObject({
      code: "UNAVAILABLE",
      statusCode: undefined,
      cause: unavailable,
    });
  });

  it("uses the raw health contract", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
      )
      .mockResolvedValueOnce(new Response("not ready", { status: 503 }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    await expect(client.health()).resolves.toBe(true);
    await expect(client.health()).resolves.toBe(false);
    expect(
      fetcher.mock.calls.map(([url, init]) => [String(url), init?.method]),
    ).toEqual([
      ["https://example.com/health", "GET"],
      ["https://example.com/health", "GET"],
    ]);
  });

  it("keeps pagination metadata separate from legacy listing results", async () => {
    const first = [{ name: "docs" }];
    const last = [{ name: "notes" }];
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        Response.json({ status: "ok", result: first, has_more: true }),
      )
      .mockResolvedValueOnce(ok(last))
      .mockResolvedValueOnce(ok(first));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    const uri = "viking://resources";

    expect(
      await client.listPage(uri, { limit: 1, extraFields: ["locked", "id"] }),
    ).toEqual({
      result: first,
      hasMore: true,
    });
    expect(await client.listPage(uri, { limit: 1, offset: 1 })).toEqual({
      result: last,
      hasMore: false,
    });
    await expect(client.list(uri)).resolves.toEqual(first);

    const requests = fetcher.mock.calls.map(([url]) => new URL(String(url)));
    const [firstPage, lastPage, legacy] = requests;
    expect(firstPage!.searchParams.getAll("extra_fields")).toEqual([
      "locked",
      "id",
    ]);
    expect(lastPage!.searchParams.get("offset")).toBe("1");
    expect(legacy!.searchParams.has("offset")).toBe(false);
  });

  it("sets and clears tags while protecting official fields", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({ updated: 1 }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.setTags("resources/demo.md", ["team=search"], {
      extra: { future_flag: false },
    });
    await client.setTags("resources/demo.md", undefined, { mode: "clear" });
    expect(
      fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body))),
    ).toEqual([
      {
        uri: "viking://resources/demo.md",
        tags: ["team=search"],
        mode: "replace",
        recursive: false,
        future_flag: false,
      },
      { uri: "viking://resources/demo.md", mode: "clear", recursive: false },
    ]);
    expect(() =>
      client.setTags("resources/demo.md", ["team=search"], {
        extra: { uri: "viking://other" },
      }),
    ).toThrow("extra cannot override uri");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("encodes local image bytes with their MIME type", async () => {
    const directory = await mkdtemp(join(tmpdir(), "openviking-sdk-image-"));
    try {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockImplementation(async () => ok({}));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
      });
      for (const [name, bytes, dataUri] of [
        ["photo.png", [137, 80, 78, 71], "data:image/png;base64,iVBORw=="],
        ["photo.bmp", [66, 77], "data:image/bmp;base64,Qk0="],
      ] as const) {
        const path = join(directory, name);
        await writeFile(path, new Uint8Array(bytes));
        await client.find("", { image: path });
        expect(
          JSON.parse(String(fetcher.mock.lastCall![1]?.body)).image_url,
        ).toBe(dataUri);
      }
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

  it("serializes explicit policy disable and clears event tags for one commit", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    await client.createSession({ sessionId: "tagged", autoCommitPolicy: null });
    await client.updateSessionConfig("tagged", { autoCommitPolicy: null });
    await client.commitSession("tagged", { keepRecentCount: 0, eventTags: [] });

    expect(
      fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body))),
    ).toEqual([
      { session_id: "tagged", auto_commit_policy: null },
      { auto_commit_policy: null },
      { keep_recent_count: 0, extraction_metadata: { event: { tags: [] } } },
    ]);
  });

  it("returns null for a missing task and propagates a permission failure", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 404 }))
      .mockResolvedValueOnce(
        Response.json(
          {
            status: "error",
            error: { code: "FORBIDDEN", message: "denied" },
          },
          { status: 403 },
        ),
      );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await expect(client.getTask("missing")).resolves.toBeNull();
    await expect(client.getTask("restricted")).rejects.toMatchObject({
      code: "FORBIDDEN",
      statusCode: 403,
    });
  });

  it("requires a name and separates persistent Compile creation from execution", async () => {
    const compileName = "research-summary";
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(ok({ compile_name: compileName }))
      .mockResolvedValueOnce(ok({ task_id: "cmp_1" }))
      .mockResolvedValueOnce(ok({ task_id: "cmp_memory" }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    const input = {
      from: ["viking://resources/source"],
      to: "viking://resources/output",
      skill: "viking://agent/skills/wiki",
      args: { auto_update: false },
    };
    // @ts-expect-error JavaScript callers must also supply compile_name.
    expect(() => client.createCompileTask(input)).toThrow(
      "compile_name is required",
    );
    expect(() =>
      client.createCompileTask({ ...input, compile_name: "" }),
    ).toThrow("compile_name is required");
    expect(fetcher).not.toHaveBeenCalled();
    const namedInput = { ...input, compile_name: compileName };
    expect(await client.createCompileTask(namedInput)).toEqual({
      compile_name: compileName,
    });
    const args = { owner_user_id: "alice" };
    await client.compile({ compile_name: compileName, args });
    expect(String(fetcher.mock.calls[0]![0])).toBe(
      "https://example.com/api/v1/compile/create",
    );
    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual(
      namedInput,
    );
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toEqual({
      compile_name: compileName,
      args,
    });
    const memory = { to: "viking://~/memories", skill: "memory" } as const;
    await expect(client.compile(memory)).resolves.toEqual({
      task_id: "cmp_memory",
    });
    expect(JSON.parse(String(fetcher.mock.calls[2]![1]?.body))).toEqual(memory);
  });

  it("preserves an explicitly empty session message", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.addMessage("session", { role: "assistant", content: "" });
    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toMatchObject({
      content: "",
    });
  });

  it.each(["addSkill", "updateSkill"] as const)(
    "%s sends long inline Markdown without a local upload",
    async (method) => {
      const source =
        "---\nname: demo\ndescription: An inline skill\n---\n" +
        "Follow these instructions carefully.\n".repeat(20);
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
      });

      await expect(
        method === "addSkill"
          ? client.addSkill(source)
          : client.updateSkill("demo", source),
      ).resolves.toEqual({});

      expect(fetcher).toHaveBeenCalledTimes(1);
      const [url, init] = fetcher.mock.calls[0]!;
      expect(String(url)).toBe(
        `https://example.com/api/v1/skills${method === "addSkill" ? "" : "/demo"}`,
      );
      expect(init?.method).toBe(method === "addSkill" ? "POST" : "PUT");
      expect(JSON.parse(String(init?.body))).toEqual({
        wait: false,
        data: source,
      });
    },
  );

  it.each(["addResource", "importOVPack", "restoreOVPack"] as const)(
    "%s preserves local path errors before sending a request",
    async (method) => {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok({}));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
      });
      const source = join(tmpdir(), "x".repeat(300));

      await expect(
        method === "importOVPack"
          ? client.importOVPack(source, "resources")
          : client[method](source),
      ).rejects.toMatchObject({ code: "ENAMETOOLONG" });
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("uploads local file contents and directory archives through multipart", async () => {
    const directory = await mkdtemp(join(tmpdir(), "openviking-sdk-"));
    try {
      const path = join(directory, "resource.md");
      const project = join(directory, "project");
      await mkdir(join(project, "docs"), { recursive: true });
      await writeFile(path, "local content");
      await writeFile(join(project, "docs", "readme.md"), "# Project");

      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(ok({ temp_file_id: "temp-file" }))
        .mockResolvedValueOnce(ok({ root_uri: "viking://resources/resource" }))
        .mockResolvedValueOnce(ok({ temp_file_id: "temp-zip" }))
        .mockResolvedValueOnce(ok({ root_uri: "viking://resources/project" }));
      const client = new OpenVikingClient({
        baseUrl: "https://example.com",
        fetch: fetcher,
        uploadMode: "shared",
      });

      await expect(client.addResource(path)).resolves.toEqual({
        root_uri: "viking://resources/resource",
      });
      await expect(client.addResource(project)).resolves.toEqual({
        root_uri: "viking://resources/project",
      });
      expect(
        fetcher.mock.calls.map(([url]) => new URL(String(url)).pathname),
      ).toEqual([
        "/api/v1/resources/temp_upload",
        "/api/v1/resources",
        "/api/v1/resources/temp_upload",
        "/api/v1/resources",
      ]);

      const fileForm = fetcher.mock.calls[0]![1]?.body as FormData;
      const file = fileForm.get("file") as File;
      expect(fileForm.get("upload_mode")).toBe("shared");
      expect(file.name).toBe("resource.md");
      expect(await file.text()).toBe("local content");

      const zipForm = fetcher.mock.calls[2]![1]?.body as FormData;
      const zip = zipForm.get("file") as File;
      expect(zipForm.get("upload_mode")).toBe("shared");
      expect(zip.name).toBe("project.zip");
      const entries = unzipSync(new Uint8Array(await zip.arrayBuffer()));
      expect(Object.keys(entries)).toEqual(["docs/readme.md"]);
      expect(strFromU8(entries["docs/readme.md"]!)).toBe("# Project");

      for (const [requestIndex, sourceName, tempFileId] of [
        [1, "resource.md", "temp-file"],
        [3, "project", "temp-zip"],
      ] as const) {
        const body = JSON.parse(
          String(fetcher.mock.calls[requestIndex]![1]?.body),
        );
        expect(body).toMatchObject({
          source_name: sourceName,
          temp_file_id: tempFileId,
        });
        expect(body).not.toHaveProperty("path");
      }
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

  it("preserves structured and plain HTTP errors on OVPack downloads", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "error",
            error: { code: "FORBIDDEN", message: "denied" },
          }),
          { status: 403 },
        ),
      )
      .mockResolvedValueOnce(new Response("gateway failure", { status: 502 }));
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await expect(client.backupOVPack("backup")).rejects.toMatchObject({
      code: "FORBIDDEN",
      statusCode: 403,
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

  it("keeps caller cancellation distinct from timeouts on OVPack downloads", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (_input, init) => {
        const signal = init?.signal;
        if (signal?.aborted) throw signal.reason;
        return new Response(new Uint8Array([1]));
      });
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });
    for (const reason of [
      new Error("cancelled"),
      new OpenVikingError("cancelled", { code: "UNAVAILABLE" }),
    ]) {
      const controller = new AbortController();
      controller.abort(reason);
      await expect(
        client.backupOVPack("backup", false, { signal: controller.signal }),
      ).rejects.toMatchObject({
        code: "ABORTED",
        message: "Request was aborted by the caller",
      });
    }

    let markStarted: (() => void) | undefined;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    fetcher.mockImplementationOnce(
      async (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = init?.signal;
          signal?.addEventListener("abort", () => reject(signal.reason), {
            once: true,
          });
          markStarted?.();
        }),
    );
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

  it("keeps snapshot restore read-only when requested and decodes binary revisions", async () => {
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
      );
    const client = new OpenVikingClient({
      baseUrl: "https://example.com",
      fetch: fetcher,
    });

    await client.gitRestore({ sourceCommit: "old", dryRun: true });
    const [restoreUrl, restore] = fetcher.mock.calls[0]!;
    expect(new URL(String(restoreUrl)).pathname).toBe(
      "/api/v1/snapshot/restore",
    );
    expect(JSON.parse(String(restore?.body))).toMatchObject({
      source_commit: "old",
      dry_run: true,
    });

    await expect(
      client.gitShow("main", "viking://resources/a"),
    ).resolves.toEqual({
      oid: "blob-1",
      size: 2,
      bytes: new Uint8Array([4, 5]),
    });
  });
});
