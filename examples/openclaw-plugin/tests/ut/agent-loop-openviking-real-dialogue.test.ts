import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { once } from "node:events";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { describe, expect, it } from "vitest";

import { OpenVikingClient, type OVMessage } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";

type AgentCtor = new (opts: Record<string, unknown>) => {
  prompt: (input: string) => Promise<void>;
  state: { messages: AgentMessage[] };
  subscribe: (listener: (event: Record<string, unknown>) => void) => () => void;
  transformContext?: (messages: AgentMessage[], signal?: AbortSignal) => Promise<AgentMessage[]>;
};

type AgentMessage = {
  role?: string;
  content?: unknown;
  toolCallId?: string;
  toolName?: string;
};

const pluginRoot = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const repoRoot = path.resolve(pluginRoot, "../..");
const openclawSourceRoot = process.env.OPENCLAW_SOURCE_ROOT ?? "D:/agent/TeamCode/openclaw";
const openclawRuntimeRoot =
  process.env.OPENCLAW_RUNTIME_ROOT ?? "E:/work_memory/OpenClaw/openclaw-runtime/node_modules/openclaw";

const realDescribe = process.env.OPENVIKING_REAL_E2E === "1" ? describe : describe.skip;

realDescribe("real dialogue: AgentLoop + OpenViking HTTP server", () => {
  it("captures multi-tool dialogue once when the finalizer replays the full transcript", async () => {
    const server = await startOpenVikingServer();
    const lines: string[] = [];
    const log = (scope: string, message: string) => {
      const line = `[${scope.padEnd(34, " ")}] ${message}`;
      lines.push(line);
      console.log(line);
    };

    try {
      const { Agent } = await importModule<{ Agent: AgentCtor }>(
        `${openclawSourceRoot}/node_modules/@mariozechner/pi-agent-core/dist/index.js`,
      );
      const { l: installContextEngineLoopHook } = await importModule<{
        l: (params: Record<string, unknown>) => () => void;
      }>(`${openclawRuntimeRoot}/dist/tool-result-truncation-C9VMA-ii.js`);
      const { o: finalizeHarnessContextEngineTurn } = await importModule<{
        o: (params: Record<string, unknown>) => Promise<void>;
      }>(`${openclawRuntimeRoot}/dist/attempt.tool-run-context-8Hyfvp_Y.js`);

      const sessionId = randomUUID();
      const agentId = "real-dialogue-agent";
      const sessionKey = `agent:main:${sessionId}`;
      const prompt = "Please call demo_tool twice, then give the final answer.";
      const ovClient = new OpenVikingClient(
        server.baseUrl,
        "",
        agentId,
        20_000,
      );

      const cfg = memoryOpenVikingConfigSchema.parse({
        mode: "remote",
        baseUrl: server.baseUrl,
        autoCapture: true,
        autoRecall: false,
        commitTokenThreshold: 20000,
        emitStandardDiagnostics: true,
      });

      const baseEngine = createMemoryOpenVikingContextEngine({
        id: "openviking",
        name: "OpenViking Real Dialogue Engine",
        version: "real-dialogue-test",
        cfg,
        logger: {
          info: (msg) => log("ov.logger.info", msg),
          warn: (msg) => log("ov.logger.warn", msg),
          error: (msg) => log("ov.logger.error", msg),
        },
        getClient: async () => ovClient,
        resolveAgentId: () => agentId,
      });

      const afterTurnCalls: Array<{ source: string; total: number; pre: number; roles: string }> = [];
      const assembleCalls: Array<{ input: number; roles: string }> = [];
      const contextEngine = {
        ...baseEngine,
        async afterTurn(params: Record<string, unknown>) {
          const messages = (params.messages ?? []) as AgentMessage[];
          const prePromptMessageCount = Number(params.prePromptMessageCount ?? 0);
          const source = runtimeSource(params);
          afterTurnCalls.push({
            source,
            total: messages.length,
            pre: prePromptMessageCount,
            roles: roles(messages),
          });
          log(
            "ov.afterTurn.call",
            `source=${source} total=${messages.length} pre=${prePromptMessageCount} roles=[${roles(messages)}]`,
          );
          await baseEngine.afterTurn?.(params as never);
        },
        async assemble(params: Record<string, unknown>) {
          const messages = (params.messages ?? []) as AgentMessage[];
          assembleCalls.push({ input: messages.length, roles: roles(messages) });
          log("ov.assemble.call", `input=${messages.length} roles=[${roles(messages)}]`);
          return await baseEngine.assemble(params as never);
        },
      };

      const model = {
        id: "mock-multitool-model",
        provider: "diagnostic",
        api: "mock",
        input: ["text"],
        output: ["text"],
      };

      let modelCall = 0;
      const agent = new Agent({
        initialState: {
          systemPrompt: "You are a deterministic diagnostic agent.",
          model,
          thinkingLevel: "off",
          tools: [makeDemoTool(log)],
          messages: [],
        },
        toolExecution: "sequential",
        transformContext: async (messages: AgentMessage[]) => {
          log("agent.baseTransformContext", `roles=[${roles(messages)}]`);
          return messages;
        },
        convertToLlm: async (messages: AgentMessage[]) => {
          const llmMessages = messages.filter((message) =>
            ["user", "assistant", "toolResult"].includes(String(message.role)),
          );
          log("agent.convertToLlm", `roles=[${roles(llmMessages)}]`);
          return llmMessages;
        },
        streamFn: async (_model: unknown, llmContext: { messages: AgentMessage[] }) => {
          modelCall += 1;
          log(`model.streamFn #${modelCall}`, `received roles=[${roles(llmContext.messages)}]`);
          if (modelCall === 1) {
            return responseFrom(assistantWithToolCall("call_1", 1));
          }
          if (modelCall === 2) {
            return responseFrom(assistantWithToolCall("call_2", 2));
          }
          return responseFrom({
            role: "assistant",
            content: [{ type: "text", text: "final answer after two real OV tool results" }],
            stopReason: "stop",
            provider: "diagnostic",
            model: "mock-multitool-model",
            api: "mock",
            timestamp: Date.now(),
          });
        },
      });

      agent.subscribe((event) => {
        if (event.type === "turn_end") {
          log(
            "event.turn_end",
            `assistantToolCalls=${toolCallsOf(event.message as AgentMessage).length} toolResults=${((event.toolResults as AgentMessage[]) ?? []).length}`,
          );
        }
      });

      installContextEngineLoopHook({
        agent,
        contextEngine,
        sessionId,
        sessionKey,
        sessionFile: `${sessionId}.jsonl`,
        tokenBudget: 128000,
        modelId: "mock-multitool-model",
        getPrePromptMessageCount: () => 0,
        getRuntimeContext: ({ messages, prePromptMessageCount }: {
          messages: AgentMessage[];
          prePromptMessageCount: number;
        }) => ({
          source: "loop-hook",
          agentId,
          sessionKey,
          messageCount: messages.length,
          prePromptMessageCount,
        }),
      });

      await agent.prompt(prompt);

      log("finalizer.call", "calling finalizeHarnessContextEngineTurn after AgentLoop completes");
      await finalizeHarnessContextEngineTurn({
        contextEngine,
        promptError: false,
        aborted: false,
        yieldAborted: false,
        sessionIdUsed: sessionId,
        sessionKey,
        sessionFile: `${sessionId}.jsonl`,
        messagesSnapshot: agent.state.messages,
        prePromptMessageCount: 0,
        tokenBudget: 128000,
        runtimeContext: {
          source: "finalizer",
          agentId,
          sessionKey,
        },
        runMaintenance: async (params: unknown) => {
          log("finalizer.maintain", JSON.stringify(params));
        },
        sessionManager: {},
        warn: (msg: string) => log("finalizer.warn", msg),
      });

      const tail = await ovClient.getSessionMessagesTail(sessionId, 64, agentId);
      const storedRoles = tail.messages.map((message) => message.role);
      const storedTexts = tail.messages.map(messageText);
      log("ov.real.tail", `count=${tail.messages.length} roles=[${storedRoles.join(" -> ")}]`);
      storedTexts.forEach((text, index) => log(`ov.real.tail[${index}]`, text));

      expect(modelCall).toBe(3);
      expect(agent.state.messages.map((message) => message.role)).toEqual([
        "user",
        "assistant",
        "toolResult",
        "assistant",
        "toolResult",
        "assistant",
      ]);
      expect(afterTurnCalls.map((call) => call.source)).toEqual([
        "loop-hook",
        "loop-hook",
        "loop-hook",
        "finalizer",
      ]);
      expect(afterTurnCalls.map((call) => call.pre)).toEqual([0, 1, 3, 0]);
      expect(assembleCalls).toHaveLength(3);

      expect(storedRoles).toEqual([
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
      ]);
      expect(countContaining(tail.messages, prompt)).toBe(1);
      expect(countContaining(tail.messages, "tool result for step 1")).toBe(1);
      expect(countContaining(tail.messages, "tool result for step 2")).toBe(1);
      expect(countContaining(tail.messages, "final answer after two real OV tool results")).toBe(1);
      expect(lines.some((line) => line.includes("afterTurn_tail_dedup"))).toBe(true);
    } finally {
      await server.stop();
    }
  }, 120_000);
});

async function importModule<T>(file: string): Promise<T> {
  return await import(pathToFileURL(path.normalize(file)).href) as T;
}

function makeDemoTool(log: (scope: string, message: string) => void) {
  return {
    name: "demo_tool",
    label: "Demo Tool",
    description: "A deterministic diagnostic tool for real OpenViking dialogue integration.",
    parameters: {
      type: "object",
      properties: {
        step: { type: "number" },
      },
      required: ["step"],
      additionalProperties: false,
    },
    async execute(toolCallId: string, params: { step: number }) {
      log("tool.execute", `${toolCallId} params=${JSON.stringify(params)}`);
      return {
        content: [{ type: "text", text: `tool result for step ${params.step}` }],
        details: { ok: true, step: params.step },
      };
    },
  };
}

function assistantWithToolCall(id: string, step: number) {
  return {
    role: "assistant",
    content: [
      { type: "text", text: `assistant asks for demo_tool step=${step}` },
      {
        type: "toolCall",
        id,
        name: "demo_tool",
        arguments: { step },
      },
    ],
    stopReason: "toolUse",
    provider: "diagnostic",
    model: "mock-multitool-model",
    api: "mock",
    timestamp: Date.now(),
  };
}

function responseFrom(finalMessage: AgentMessage) {
  return {
    async *[Symbol.asyncIterator]() {
      yield { type: "done" };
    },
    async result() {
      return finalMessage;
    },
  };
}

function runtimeSource(params: Record<string, unknown>) {
  const runtimeContext = params.runtimeContext as Record<string, unknown> | undefined;
  return String(runtimeContext?.source ?? "unknown");
}

function roles(messages: AgentMessage[]) {
  return messages.map((message) => message.role ?? "unknown").join(" -> ");
}

function toolCallsOf(message: AgentMessage) {
  return Array.isArray(message.content)
    ? (message.content as Array<Record<string, unknown>>).filter((part) => part.type === "toolCall")
    : [];
}

function messageText(message: OVMessage): string {
  return message.parts
    .map((part) => {
      if (part.type === "text") return part.text ?? "";
      if (part.type === "tool") return part.tool_output ?? "";
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

function countContaining(messages: OVMessage[], text: string): number {
  return messages.filter((message) => messageText(message).includes(text)).length;
}

async function startOpenVikingServer(): Promise<{
  baseUrl: string;
  stop: () => Promise<void>;
}> {
  const tempRoot = process.env.OPENVIKING_TEST_TMP ?? (process.platform === "win32" ? "F:\\pytest-tmp" : tmpdir());
  await mkdir(tempRoot, { recursive: true });
  const dataDir = await mkdtemp(path.join(tempRoot, "ov-real-dialogue-"));
  const port = await findFreePort();
  const python = process.env.OPENVIKING_TEST_PYTHON ?? path.join(repoRoot, ".venv", "Scripts", "python.exe");
  const helper = path.join(pluginRoot, "tests", "ut", "fixtures", "real-ov-test-server.py");
  const output: string[] = [];
  const child = spawn(
    python,
    [helper, "--port", String(port), "--data-dir", dataDir],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        PYTHONPATH: [repoRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
        TEMP: tempRoot,
        TMP: tempRoot,
      },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );

  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => output.push(String(chunk)));
  child.stderr.on("data", (chunk) => output.push(String(chunk)));

  await waitForServerReady(child, output);

  return {
    baseUrl: `http://127.0.0.1:${port}`,
    async stop() {
      child.stdin.end("\n");
      const exited = once(child, "exit");
      const timedOut = sleep(15_000).then(() => {
        child.kill();
        return once(child, "exit");
      });
      await Promise.race([exited, timedOut]);
      await rm(dataDir, { recursive: true, force: true }).catch(() => undefined);
    },
  };
}

async function waitForServerReady(
  child: ChildProcessWithoutNullStreams,
  output: string[],
): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`OpenViking test server did not become ready:\n${output.join("")}`));
    }, 60_000);

    const onData = (chunk: Buffer | string) => {
      const text = String(chunk);
      if (text.includes("OPENVIKING_TEST_SERVER_READY")) {
        clearTimeout(timer);
        cleanup();
        resolve();
      }
    };
    const onExit = (code: number | null, signal: NodeJS.Signals | null) => {
      clearTimeout(timer);
      cleanup();
      reject(new Error(`OpenViking test server exited early code=${code} signal=${signal}:\n${output.join("")}`));
    };
    const cleanup = () => {
      child.stdout.off("data", onData);
      child.stderr.off("data", onData);
      child.off("exit", onExit);
    };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.once("exit", onExit);
  });
}

async function findFreePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("Could not allocate local port")));
        return;
      }
      const port = address.port;
      server.close(() => resolve(port));
    });
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
