import assert from "node:assert/strict";
import test from "node:test";

import { createExperienceToolProvider } from "./experience-tools.mjs";

const config = {
  baseUrl: "http://openviking.test",
  apiKey: "test-key",
  account: "new",
  user: "test",
  peerId: "peer-a",
};

function toolPayload(result) {
  assert.equal(result.isError, undefined);
  assert.equal(result.content.length, 1);
  assert.equal(result.content[0].type, "text");
  return JSON.parse(result.content[0].text);
}

test("lists the two official Experience tools", () => {
  const provider = createExperienceToolProvider({ fetchImpl: async () => assert.fail("unexpected fetch") });
  assert.deepEqual(
    provider.listTools().map((tool) => tool.name),
    ["search_experience", "read_experience"],
  );
});

test("search_experience searches only the current user's Experience directory", async () => {
  const calls = [];
  const provider = createExperienceToolProvider({
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      if (options.method === "GET") {
        return new Response(JSON.stringify({
          result: '## Approach\n先验证身份。\n\n<!-- MEMORY_FIELDS\n{"status":"active","version":3,"curated_at":"2026-08-10","from":["case-17"]}\n-->',
        }), { status: 200, headers: { "content-type": "application/json" } });
      }
      return new Response(JSON.stringify({
        ok: true,
        result: {
          memories: [
            {
              uri: "viking://user/test/memories/experiences/无订单号换货处理.md",
              score: 0.82,
              abstract: "先验证身份，再逐个定位订单。",
            },
            {
              uri: "viking://user/test/memories/preferences/回复风格.md",
              score: 0.91,
              abstract: "回复简洁。",
            },
            {
              uri: "viking://user/test/memories/experiences/.abstract.md",
              score: 0.95,
              abstract: "Experience 目录摘要。",
            },
            {
              uri: "viking://user/test/memories/experiences/.overview.md",
              score: 0.94,
              abstract: "Experience 目录概览。",
            },
            {
              uri: "viking://user/test/memories/experiences/无订单号换货处理.md?source=codex",
              score: 0.93,
              abstract: "非规范 URI 别名。",
            },
            {
              uri: "viking://user/test/memories/experiences/无订单号换货处理.md#approach",
              score: 0.92,
              abstract: "带片段的 URI 别名。",
            },
          ],
          resources: [],
          skills: [],
        },
      }), { status: 200, headers: { "content-type": "application/json" } });
    },
  });

  const result = await provider.callTool(
    { name: "search_experience", arguments: { query: "无订单号换货", limit: 5 } },
    { config },
  );

  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "http://openviking.test/api/v1/search/find");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers.Authorization, "Bearer test-key");
  assert.equal(calls[0].options.headers["X-OpenViking-Account"], "new");
  assert.equal(calls[0].options.headers["X-OpenViking-User"], "test");
  assert.equal(calls[0].options.headers["X-OpenViking-Actor-Peer"], "peer-a");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    query: "无订单号换货",
    target_uri: "viking://user/memories/experiences/",
    limit: 5,
  });
  assert.deepEqual(toolPayload(result), {
    results: [
      {
        uri: "viking://user/test/memories/experiences/无订单号换货处理.md",
        title: "无订单号换货处理",
        score: 0.82,
        snippet: "先验证身份，再逐个定位订单。",
        metadata: {
          status: "active",
          version: 3,
          curated_at: "2026-08-10",
          curated_from: ["case-17"],
        },
      },
    ],
  });
});

test("read_experience reads a canonical Experience URI", async () => {
  const calls = [];
  const uri = "viking://user/test/memories/experiences/无订单号换货处理.md";
  const provider = createExperienceToolProvider({
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return new Response(JSON.stringify({
        ok: true,
        result: '## Approach\n先验证用户身份。\n\n<!-- MEMORY_FIELDS\n{"status":"draft","version":2,"curated_from":{"project":"returns"},"source_extraction_id":"hidden"}\n-->',
      }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await provider.callTool(
    { name: "read_experience", arguments: { uri } },
    { config },
  );

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    `http://openviking.test/api/v1/content/read?uri=${encodeURIComponent(uri)}&raw=true`,
  );
  assert.equal(calls[0].options.method, "GET");
  assert.deepEqual(toolPayload(result), {
    uri,
    content: "## Approach\n先验证用户身份。",
    metadata: {
      status: "draft",
      version: 2,
      curated_from: { project: "returns" },
    },
  });
});

test("search_experience excludes deprecated and archived hits while keeping draft explicit", async () => {
  const statuses = new Map([
    ["active.md", "active"],
    ["draft.md", "draft"],
    ["deprecated.md", "deprecated"],
    ["archived.md", "ARCHIVED"],
  ]);
  const provider = createExperienceToolProvider({
    fetchImpl: async (url, options) => {
      if (options.method === "POST") {
        return new Response(JSON.stringify({
          result: {
            memories: [...statuses.keys()].map((name, index) => ({
              uri: `viking://user/test/memories/experiences/${name}`,
              score: 1 - index / 10,
              abstract: name,
            })),
          },
        }), { status: 200 });
      }
      const name = new URL(String(url)).searchParams.get("uri").split("/").at(-1);
      return new Response(JSON.stringify({
        result: `content\n\n<!-- MEMORY_FIELDS\n${JSON.stringify({ status: statuses.get(name), version: 1 })}\n-->`,
      }), { status: 200 });
    },
  });

  const response = await provider.callTool(
    { name: "search_experience", arguments: { query: "status" } },
    { config },
  );
  const payload = toolPayload(response);
  assert.deepEqual(payload.results.map((item) => item.title), ["active", "draft"]);
  assert.equal(payload.results[1].metadata.status, "draft");
});

test("search_experience drops candidates when authoritative raw reads fail", async () => {
  const provider = createExperienceToolProvider({
    fetchImpl: async (_url, options) => {
      if (options.method === "GET") return new Response("missing", { status: 404 });
      return new Response(JSON.stringify({
        result: {
          memories: [{
            uri: "viking://user/test/memories/experiences/legacy.md",
            score: 0.5,
            abstract: "legacy server",
          }],
        },
      }), { status: 200 });
    },
  });

  const response = await provider.callTool(
    { name: "search_experience", arguments: { query: "legacy" } },
    { config },
  );
  assert.deepEqual(toolPayload(response).results, []);
});

test("Experience tools fail closed on malformed lifecycle metadata", async () => {
  const uri = "viking://user/test/memories/experiences/malformed.md";
  const provider = createExperienceToolProvider({
    fetchImpl: async (_url, options) => {
      if (options.method === "POST") {
        return new Response(JSON.stringify({
          result: { memories: [{ uri, score: 0.7, abstract: "malformed" }] },
        }), { status: 200 });
      }
      return new Response(JSON.stringify({
        result: "content\n\n<!-- MEMORY_FIELDS\n{not-json}\n-->",
      }), { status: 200 });
    },
  });

  const searchResponse = await provider.callTool(
    { name: "search_experience", arguments: { query: "malformed" } },
    { config },
  );
  assert.deepEqual(toolPayload(searchResponse).results, []);

  const readResponse = await provider.callTool(
    { name: "read_experience", arguments: { uri } },
    { config },
  );
  assert.equal(readResponse.isError, true);
  assert.match(readResponse.content[0].text, /malformed Experience lifecycle metadata/);
});

test("read_experience rejects deprecated and archived lifecycle states", async () => {
  const provider = createExperienceToolProvider({
    fetchImpl: async (url) => {
      const uri = new URL(String(url)).searchParams.get("uri");
      const status = uri.endsWith("archived.md") ? "ARCHIVED" : "deprecated";
      return new Response(JSON.stringify({
        result: `content\n\n<!-- MEMORY_FIELDS\n${JSON.stringify({ status })}\n-->`,
      }), { status: 200 });
    },
  });

  for (const status of ["deprecated", "archived"]) {
    const response = await provider.callTool(
      {
        name: "read_experience",
        arguments: {
          uri: `viking://user/test/memories/experiences/${status}.md`,
        },
      },
      { config },
    );
    assert.equal(response.isError, true);
    assert.match(response.content[0].text, new RegExp(`status=${status}`));
  }
});

test("read_experience rejects non-Experience URIs without an HTTP request", async () => {
  let callCount = 0;
  const provider = createExperienceToolProvider({
    fetchImpl: async () => {
      callCount += 1;
      return new Response();
    },
  });

  const result = await provider.callTool(
    {
      name: "read_experience",
      arguments: { uri: "viking://user/test/memories/preferences/回复风格.md" },
    },
    { config },
  );

  assert.equal(callCount, 0);
  assert.equal(result.isError, true);
  assert.match(result.content[0].text, /Experience URI/);
});

test("read_experience rejects noncanonical Experience URI aliases", async () => {
  let callCount = 0;
  const provider = createExperienceToolProvider({
    fetchImpl: async () => {
      callCount += 1;
      return new Response();
    },
  });
  const canonicalUri = "viking://user/test/memories/experiences/无订单号换货处理.md";

  for (const uri of [`${canonicalUri}?source=codex`, `${canonicalUri}#approach`]) {
    const result = await provider.callTool(
      { name: "read_experience", arguments: { uri } },
      { config },
    );
    assert.equal(result.isError, true);
  }
  assert.equal(callCount, 0);
});

test("read_experience rejects internal Experience sidecars", async () => {
  let callCount = 0;
  const provider = createExperienceToolProvider({
    fetchImpl: async () => {
      callCount += 1;
      return new Response();
    },
  });

  for (const name of [".abstract.md", ".overview.md", ".relations.json"]) {
    const result = await provider.callTool(
      {
        name: "read_experience",
        arguments: { uri: `viking://user/test/memories/experiences/${name}` },
      },
      { config },
    );
    assert.equal(result.isError, true);
  }
  assert.equal(callCount, 0);
});

test("read_experience accepts other dot-prefixed Experience files", async () => {
  let callCount = 0;
  const uri = "viking://user/test/memories/experiences/.custom-experience.md";
  const provider = createExperienceToolProvider({
    fetchImpl: async () => {
      callCount += 1;
      return new Response(JSON.stringify({ result: "content" }), { status: 200 });
    },
  });

  const result = await provider.callTool(
    { name: "read_experience", arguments: { uri } },
    { config },
  );

  assert.equal(result.isError, undefined);
  assert.equal(callCount, 1);
});

test("Experience HTTP requests inherit the configured timeout signal", async () => {
  const calls = [];
  const uri = "viking://user/test/memories/experiences/无订单号换货处理.md";
  const provider = createExperienceToolProvider({
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      if (String(url).includes("/search/find")) {
        return new Response(JSON.stringify({ result: { memories: [] } }), { status: 200 });
      }
      return new Response(JSON.stringify({ result: "content" }), { status: 200 });
    },
  });
  const timeoutConfig = { ...config, timeoutMs: 1234 };

  await provider.callTool(
    { name: "search_experience", arguments: { query: "换货" } },
    { config: timeoutConfig },
  );
  await provider.callTool(
    { name: "read_experience", arguments: { uri } },
    { config: timeoutConfig },
  );

  assert.equal(calls.length, 2);
  for (const call of calls) assert.ok(call.options.signal instanceof AbortSignal);
});
