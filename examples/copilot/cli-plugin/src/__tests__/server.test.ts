import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { CallToolResultSchema, type CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { describe, expect, it } from "vitest";
import type { OVResult, OVTurn, RecallHit, RecallOptions, ReadOptions, CommitOptions } from "@openviking/copilot-shared";
import { createOpenVikingMcpServer, type OpenVikingToolClient } from "../server.js";

const execFileAsync = promisify(execFile);
const cliRoot = fileURLToPath(new URL("../../", import.meta.url));

class FakeOVClient implements OpenVikingToolClient {
  healthResult: OVResult<unknown> = { ok: true, value: { status: "ok" } };
  recallResult: OVResult<RecallHit[]> = { ok: true, value: [{ uri: "viking://m/1", score: 0.9 }] };
  readResult: OVResult<string> = { ok: true, value: "memory body" };
  appendResult: OVResult<unknown> = { ok: true, value: { written: 1 } };
  commitResult: OVResult<unknown> = { ok: true, value: { committed: true } };
  forgetResult: OVResult<unknown> = { ok: true, value: { deleted: true } };

  recallCalls: Array<{ query: string; opts: RecallOptions }> = [];
  readCalls: Array<{ uri: string; opts?: ReadOptions }> = [];
  appendCalls: Array<{ sessionId: string; turns: OVTurn[] }> = [];
  commitCalls: Array<{ sessionId: string; opts?: CommitOptions }> = [];
  forgetCalls: Array<{ uri: string; opts?: { recursive?: boolean } }> = [];

  async health(): Promise<OVResult<unknown>> {
    return this.healthResult;
  }

  async recall(query: string, opts: RecallOptions): Promise<OVResult<RecallHit[]>> {
    this.recallCalls.push({ query, opts });
    return this.recallResult;
  }

  async read(uri: string, opts?: ReadOptions): Promise<OVResult<string>> {
    this.readCalls.push({ uri, opts });
    return this.readResult;
  }

  async appendTurns(sessionId: string, turns: OVTurn[]): Promise<OVResult<unknown>> {
    this.appendCalls.push({ sessionId, turns });
    return this.appendResult;
  }

  async commit(sessionId: string, opts?: CommitOptions): Promise<OVResult<unknown>> {
    this.commitCalls.push({ sessionId, opts });
    return this.commitResult;
  }

  async forget(uri: string, opts?: { recursive?: boolean }): Promise<OVResult<unknown>> {
    this.forgetCalls.push({ uri, opts });
    return this.forgetResult;
  }
}

async function withMcpClient<T>(fake: FakeOVClient, cb: (client: Client) => Promise<T>): Promise<T> {
  const server = createOpenVikingMcpServer({
    client: fake,
    config: { recallLimit: 4, scoreThreshold: 0.2 },
    version: "test",
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "vitest", version: "0.0.0" });
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  try {
    return await cb(client);
  } finally {
    await client.close();
    await server.close();
  }
}

async function callTool(client: Client, name: string, args: Record<string, unknown> = {}): Promise<CallToolResult> {
  const result = await client.callTool({ name, arguments: args }, CallToolResultSchema);
  if (!("content" in result)) throw new Error("Expected a call tool content result");
  return result;
}

function textOf(result: CallToolResult): string {
  const first = result.content[0];
  if (!first || first.type !== "text") throw new Error("Expected first content block to be text");
  return first.text;
}

describe("OpenViking MCP tools", () => {
  it("registers the Phase 2 recall tool set without capture creep", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      const list = await client.listTools();
      expect(list.tools.map((tool) => tool.name).sort()).toEqual([
        "openviking_forget",
        "openviking_health",
        "openviking_read",
        "openviking_recall",
        "openviking_search",
        "openviking_store",
      ]);
    });
  });

  it("calls health and returns JSON text", async () => {
    const fake = new FakeOVClient();
    fake.healthResult = { ok: true, value: { status: "ok", version: "dev" } };
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_health");
      expect(JSON.parse(textOf(result))).toEqual({ status: "ok", version: "dev" });
    });
  });

  it("passes search arguments to OVClient.recall with config defaults", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_search", { query: "auth migration" });
      expect(JSON.parse(textOf(result))).toEqual([{ uri: "viking://m/1", score: 0.9 }]);
    });
    expect(fake.recallCalls).toEqual([{ query: "auth migration", opts: { limit: 4, sessionId: "", scoreThreshold: 0.2 } }]);
  });

  it("passes explicit search scope arguments to OVClient.recall", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      await callTool(client, "openviking_search", {
        query: "billing",
        limit: 2,
        targetUri: "viking://agent/memories",
        scoreThreshold: 0.7,
        sessionId: "cp-123",
      });
    });
    expect(fake.recallCalls[0]).toEqual({
      query: "billing",
      opts: {
        limit: 2,
        sessionId: "cp-123",
        targetUri: "viking://agent/memories",
        scoreThreshold: 0.7,
      },
    });
  });

  it("returns ranked and formatted context for openviking_recall", async () => {
    const fake = new FakeOVClient();
    fake.recallResult = {
      ok: true,
      value: [
        { uri: "viking://m/low", score: 0.1, type: "memory", abstract: "low score" },
        { uri: "viking://m/high", score: 0.9, type: "memory", abstract: "matched memory" },
      ],
    };
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_recall", { query: "auth migration", sessionId: "cp-123" });
      const text = textOf(result);
      expect(text).toContain("<openviking-context>");
      expect(text).toContain("matched memory");
      expect(text).not.toContain("low score");
    });
    expect(fake.recallCalls).toEqual([{ query: "auth migration", opts: { limit: 8, sessionId: "cp-123", scoreThreshold: 0 } }]);
  });

  it("reads URI content as plain text", async () => {
    const fake = new FakeOVClient();
    fake.readResult = { ok: true, value: "stored memory" };
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_read", { uri: "viking://m/1", offset: 3, limit: 10 });
      expect(textOf(result)).toBe("stored memory");
    });
    expect(fake.readCalls).toEqual([{ uri: "viking://m/1", opts: { offset: 3, limit: 10 } }]);
  });

  it("stores one turn and optionally commits the session", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_store", {
        sessionId: "cp-123",
        role: "assistant",
        content: "answer",
        commit: true,
      });
      expect(JSON.parse(textOf(result))).toEqual({ append: { written: 1 }, commit: { committed: true } });
    });
    expect(fake.appendCalls).toEqual([{ sessionId: "cp-123", turns: [{ role: "assistant", content: "answer" }] }]);
    expect(fake.commitCalls).toEqual([{ sessionId: "cp-123", opts: undefined }]);
  });

  it("forgets a URI recursively when requested", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_forget", { uri: "viking://dir", recursive: true });
      expect(JSON.parse(textOf(result))).toEqual({ deleted: true });
    });
    expect(fake.forgetCalls).toEqual([{ uri: "viking://dir", opts: { recursive: true } }]);
  });

  it("returns tool-level errors instead of throwing for OV failures", async () => {
    const fake = new FakeOVClient();
    fake.healthResult = { ok: false, error: { message: "unavailable", status: 503 } };
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_health");
      expect(result.isError).toBe(true);
      expect(textOf(result)).toContain("HTTP 503: unavailable");
    });
  });
});

describe("openviking-copilot-mcp stdio", () => {
  it("serves tools/list and bypassed health over JSON-RPC stdio", async () => {
    await execFileAsync(process.execPath, ["scripts/build.mjs"], { cwd: cliRoot });

    const transport = new StdioClientTransport({
      command: process.execPath,
      args: ["dist/mcp-server.js"],
      cwd: cliRoot,
      stderr: "pipe",
      env: {
        HOME: process.env["HOME"] ?? "",
        PATH: process.env["PATH"] ?? "",
        OPENVIKING_BYPASS_SESSION: "true",
        OPENVIKING_MEMORY_ENABLED: "true",
        OPENVIKING_URL: "http://127.0.0.1:1",
      },
    });
    const client = new Client({ name: "stdio-smoke", version: "0.0.0" });
    await client.connect(transport);
    try {
      const list = await client.listTools();
      expect(list.tools.map((tool) => tool.name)).toContain("openviking_recall");
      const result = await callTool(client, "openviking_health");
      expect(JSON.parse(textOf(result))).toEqual({ bypassed: true });
    } finally {
      await client.close();
    }
  });
});
