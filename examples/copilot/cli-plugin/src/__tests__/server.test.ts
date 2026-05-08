import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { CallToolResultSchema, type CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { describe, expect, it } from "vitest";
import type { OVResult, OVTurn, RecallHit, RecallOptions, ReadOptions, CommitOptions } from "@openviking/copilot-shared";
import { createOpenVikingMcpServer, type OpenVikingToolClient, type OpenVikingToolDeps } from "../server.js";

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

async function withMcpClient<T>(
  fake: FakeOVClient,
  cb: (client: Client) => Promise<T>,
  config: OpenVikingToolDeps["config"] = { recallLimit: 4, scoreThreshold: 0.2 },
): Promise<T> {
  const server = createOpenVikingMcpServer({
    client: fake,
    config,
    defaultSessionId: "cp-default",
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
  const result = CallToolResultSchema.parse(await client.callTool({ name, arguments: args }, CallToolResultSchema));
  if (!("content" in result)) throw new Error("Expected a call tool content result");
  return result;
}

function textOf(result: CallToolResult): string {
  const first = result.content[0];
  if (!first || first.type !== "text") throw new Error("Expected first content block to be text");
  return first.text;
}

describe("OpenViking MCP tools", () => {
  it("registers the Phase 3 CLI tool set", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      const list = await client.listTools();
      const tools = list.tools.sort((a, b) => a.name.localeCompare(b.name));
      expect(tools.map((tool) => tool.name)).toEqual([
        "openviking_capture",
        "openviking_forget",
        "openviking_health",
        "openviking_read",
        "openviking_recall",
        "openviking_search",
        "openviking_store",
      ]);
      const capture = tools.find((tool) => tool.name === "openviking_capture");
      expect(capture?.description).toContain("model-discretion based");
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

  it("captures a CLI turn via the shared sanitise and queue path", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_capture", {
        sessionId: "cp-123",
        user: "<openviking-context>recall</openviking-context>real user",
        assistant: "real reply <system-reminder>x</system-reminder>",
      });
      expect(JSON.parse(textOf(result))).toMatchObject({
        captured: 2,
        skipped: false,
        triggeredCommit: false,
        sessionId: "cp-123",
      });
    });
    expect(fake.appendCalls).toHaveLength(1);
    expect(fake.appendCalls[0]!.sessionId).toBe("cp-123");
    expect(fake.appendCalls[0]!.turns).toHaveLength(2);
    expect(fake.appendCalls[0]!.turns[0]!.content).toContain("real user");
    expect(fake.appendCalls[0]!.turns[0]!.content).not.toContain("openviking-context");
    expect(fake.appendCalls[0]!.turns[1]!.content).toContain("real reply");
    expect(fake.appendCalls[0]!.turns[1]!.content).not.toContain("system-reminder");
    expect(fake.commitCalls).toEqual([]);
  });

  it("captures into the default CLI MCP session when sessionId is omitted", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_capture", {
        user: "remember this",
        assistant: "remembered",
      });
      expect(JSON.parse(textOf(result))).toMatchObject({ captured: 2, sessionId: "cp-default" });
    });
    expect(fake.appendCalls[0]!.sessionId).toBe("cp-default");
  });

  it("triggers commits through CommitQueue when the capture threshold is crossed", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_capture", {
        sessionId: "cp-123",
        user: "commit me",
      });
      expect(JSON.parse(textOf(result))).toMatchObject({ captured: 1, triggeredCommit: true, pendingAfter: 0 });
    }, {
      captureAssistantTurns: true,
      captureMaxLength: 100_000,
      commitTokenThreshold: 1,
      writePathAsync: false,
    });
    expect(fake.commitCalls).toEqual([{ sessionId: "cp-123", opts: { force: false } }]);
  });

  it("skips capture without appending when autoCapture is disabled", async () => {
    const fake = new FakeOVClient();
    await withMcpClient(fake, async (client) => {
      const result = await callTool(client, "openviking_capture", {
        sessionId: "cp-123",
        user: "do not store",
        assistant: "not stored",
      });
      expect(JSON.parse(textOf(result))).toEqual({ captured: 0, skipped: true, reason: "autoCapture disabled" });
    }, { autoCapture: false });
    expect(fake.appendCalls).toEqual([]);
    expect(fake.commitCalls).toEqual([]);
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
      expect(list.tools.map((tool) => tool.name)).toContain("openviking_capture");
      expect(list.tools.map((tool) => tool.name)).toContain("openviking_recall");
      const result = await callTool(client, "openviking_health");
      expect(JSON.parse(textOf(result))).toEqual({ bypassed: true });
    } finally {
      await client.close();
    }
  });
});
