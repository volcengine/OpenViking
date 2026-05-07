import path from "node:path";
import { pathToFileURL } from "node:url";

import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
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

const openclawSourceRoot = "D:/agent/TeamCode/openclaw";
const openclawRuntimeRoot = "E:/work_memory/OpenClaw/openclaw-runtime/node_modules/openclaw";

describe("diagnostic: AgentLoop + OpenViking context-engine loop hook", () => {
  it("logs multi-round tool calls through real OpenViking afterTurn/assemble", async () => {
    const lines: string[] = [];
    const log = (scope: string, message: string) => {
      const line = `[${scope.padEnd(34, " ")}] ${message}`;
      lines.push(line);
      console.log(line);
    };

    const { Agent } = await importModule<{ Agent: AgentCtor }>(
      `${openclawSourceRoot}/node_modules/@mariozechner/pi-agent-core/dist/index.js`,
    );
    const { l: installContextEngineLoopHook } = await importModule<{
      l: (params: Record<string, unknown>) => () => void;
    }>(`${openclawRuntimeRoot}/dist/tool-result-truncation-C9VMA-ii.js`);
    const { o: finalizeHarnessContextEngineTurn } = await importModule<{
      o: (params: Record<string, unknown>) => Promise<void>;
    }>(`${openclawRuntimeRoot}/dist/attempt.tool-run-context-8Hyfvp_Y.js`);

    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: true,
      autoRecall: false,
      commitTokenThreshold: 20000,
      emitStandardDiagnostics: true,
    });

    const storedTail: Array<{
      id: string;
      role: string;
      parts: Array<Record<string, unknown>>;
      created_at: string;
    }> = [];
    const addSessionMessage = vi.fn(async (sessionId, role, parts, agentId) => {
      log(
        "ov.client.addSessionMessage",
        `session=${sessionId} role=${role} agent=${agentId} parts=${summarizeOvParts(parts)}`,
      );
      storedTail.push({
        id: `msg_${storedTail.length + 1}`,
        role: String(role),
        parts: JSON.parse(JSON.stringify(parts)),
        created_at: "2026-05-07T00:00:00.000Z",
      });
    });
    const client = {
      addSessionMessage,
      commitSession: vi.fn(async (...args: unknown[]) => {
        log("ov.client.commitSession", JSON.stringify(args));
        return { status: "accepted", task_id: "task-1", archived: false };
      }),
      getSession: vi.fn(async (...args: unknown[]) => {
        log("ov.client.getSession", JSON.stringify(args));
        return { pending_tokens: 100 };
      }),
      getSessionContext: vi.fn(async (...args: unknown[]) => {
        log("ov.client.getSessionContext", JSON.stringify(args));
        return {
          latest_archive_overview: "",
          latest_archive_id: "",
          pre_archive_abstracts: [],
          messages: [],
          estimatedTokens: 0,
          stats: {
            totalArchives: 0,
            includedArchives: 0,
            droppedArchives: 0,
            failedArchives: 0,
            activeTokens: 0,
            archiveTokens: 0,
          },
        };
      }),
      getSessionMessagesTail: vi.fn(async (...args: unknown[]) => {
        log("ov.client.getSessionMessagesTail", JSON.stringify(args));
        return { messages: storedTail };
      }),
    } as unknown as OpenVikingClient;

    const baseEngine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "OpenViking Diagnostic Engine",
      version: "demo",
      cfg,
      logger: {
        info: (msg) => log("ov.logger.info", msg),
        warn: (msg) => log("ov.logger.warn", msg),
        error: (msg) => log("ov.logger.error", msg),
      },
      getClient: async () => client,
      resolveAgentId: () => "ov-demo-agent",
    });

    const contextEngine = {
      ...baseEngine,
      async afterTurn(params: Record<string, unknown>) {
        const messages = (params.messages ?? []) as AgentMessage[];
        const prePromptMessageCount = Number(params.prePromptMessageCount ?? 0);
        log(
          "ov.afterTurn.call",
          `total=${messages.length} pre=${prePromptMessageCount} delta=[${summarizeMessages(messages.slice(prePromptMessageCount))}] source=${runtimeSource(params)}`,
        );
        await baseEngine.afterTurn?.(params as never);
        log(
          "ov.afterTurn.done",
          `storedCalls=${addSessionMessage.mock.calls.length}`,
        );
      },
      async assemble(params: Record<string, unknown>) {
        const messages = (params.messages ?? []) as AgentMessage[];
        log(
          "ov.assemble.call",
          `input=${messages.length} roles=[${roles(messages)}] tail=${messages.at(-1)?.role ?? "none"}`,
        );
        const result = await baseEngine.assemble(params as never);
        log(
          "ov.assemble.done",
          `output=${result.messages.length} roles=[${roles(result.messages)}] estimatedTokens=${result.estimatedTokens}`,
        );
        return result;
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
        log(
          `model.streamFn #${modelCall}`,
          `received roles=[${roles(llmContext.messages)}]`,
        );
        if (modelCall === 1) {
          return responseFrom(assistantWithToolCall("call_1", 1));
        }
        if (modelCall === 2) {
          return responseFrom(assistantWithToolCall("call_2", 2));
        }
        return responseFrom({
          role: "assistant",
          content: [{ type: "text", text: "final answer after two OV-captured tool results" }],
          stopReason: "stop",
          provider: "diagnostic",
          model: "mock-multitool-model",
          api: "mock",
          timestamp: Date.now(),
        });
      },
    });

    agent.subscribe((event) => {
      switch (event.type) {
        case "turn_start":
        case "agent_start":
          log(`event.${event.type}`, "");
          break;
        case "agent_end":
          log("event.agent_end", `newMessages=${((event.messages as AgentMessage[]) ?? []).length}`);
          break;
        case "turn_end":
          log(
            "event.turn_end",
            `assistantToolCalls=${toolCallsOf(event.message as AgentMessage).length} toolResults=${((event.toolResults as AgentMessage[]) ?? []).length}`,
          );
          break;
        case "message_end":
          log("event.message_end", summarizeMessage(event.message as AgentMessage));
          break;
        case "tool_execution_start":
          log(
            "event.tool_execution_start",
            `${event.toolCallId as string} ${event.toolName as string}`,
          );
          break;
        case "tool_execution_end":
          log(
            "event.tool_execution_end",
            `${event.toolCallId as string} isError=${String(event.isError)}`,
          );
          break;
      }
    });

    installContextEngineLoopHook({
      agent,
      contextEngine,
      sessionId: "ov-agent-loop-demo",
      sessionKey: "agent:main:ov-agent-loop-demo",
      sessionFile: "ov-agent-loop-demo.jsonl",
      tokenBudget: 128000,
      modelId: "mock-multitool-model",
      getPrePromptMessageCount: () => 0,
      getRuntimeContext: ({ messages, prePromptMessageCount }: {
        messages: AgentMessage[];
        prePromptMessageCount: number;
      }) => ({
        source: "loop-hook",
        agentId: "ov-demo-agent",
        sessionKey: "agent:main:ov-agent-loop-demo",
        messageCount: messages.length,
        prePromptMessageCount,
      }),
    });

    await agent.prompt("Please call demo_tool twice, then give the final answer.");

    log("finalizer.call", "calling finalizeHarnessContextEngineTurn after AgentLoop completes");
    await finalizeHarnessContextEngineTurn({
      contextEngine,
      promptError: false,
      aborted: false,
      yieldAborted: false,
      sessionIdUsed: "ov-agent-loop-demo",
      sessionKey: "agent:main:ov-agent-loop-demo",
      sessionFile: "ov-agent-loop-demo.jsonl",
      messagesSnapshot: agent.state.messages,
      prePromptMessageCount: 0,
      tokenBudget: 128000,
      runtimeContext: {
        source: "finalizer",
        agentId: "ov-demo-agent",
        sessionKey: "agent:main:ov-agent-loop-demo",
      },
      runMaintenance: async (params: unknown) => {
        log("finalizer.maintain", JSON.stringify(params));
      },
      sessionManager: {},
      warn: (msg: string) => log("finalizer.warn", msg),
    });

    log("final-state", `messages=${agent.state.messages.length} roles=[${roles(agent.state.messages)}]`);
    agent.state.messages.forEach((message, index) => {
      log(`final-state[${index}]`, summarizeMessage(message));
    });

    expect(modelCall).toBe(3);
    expect(agent.state.messages.map((message) => message.role)).toEqual([
      "user",
      "assistant",
      "toolResult",
      "assistant",
      "toolResult",
      "assistant",
    ]);
    expect(addSessionMessage.mock.calls.map((call) => call[1])).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(lines.some((line) => line.includes("afterTurn_tail_dedup"))).toBe(true);
  });
});

async function importModule<T>(file: string): Promise<T> {
  return await import(pathToFileURL(path.normalize(file)).href) as T;
}

function makeDemoTool(log: (scope: string, message: string) => void) {
  return {
    name: "demo_tool",
    label: "Demo Tool",
    description: "A deterministic diagnostic tool for AgentLoop/OpenViking integration.",
    parameters: {
      type: "object",
      properties: {
        step: { type: "number" },
      },
      required: ["step"],
      additionalProperties: false,
    },
    async execute(toolCallId: string, params: { step: number }, _signal?: AbortSignal, onUpdate?: (result: unknown) => void) {
      log("tool.execute", `${toolCallId} params=${JSON.stringify(params)}`);
      onUpdate?.({
        content: [{ type: "text", text: `partial update for step ${params.step}` }],
        details: { partial: true, step: params.step },
      });
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

function summarizeMessages(messages: AgentMessage[]) {
  if (messages.length === 0) return "(empty)";
  return messages.map(summarizeMessage).join(" | ");
}

function summarizeMessage(message: AgentMessage) {
  if (message.role === "assistant") {
    const calls = toolCallsOf(message)
      .map((call) => `${String(call.name)}:${String(call.id)}:${JSON.stringify(call.arguments ?? {})}`)
      .join(",");
    return calls
      ? `assistant{text="${textOf(message.content)}", toolCalls=[${calls}]}`
      : `assistant{text="${textOf(message.content)}"}`;
  }
  if (message.role === "toolResult") {
    return `toolResult{for=${message.toolCallId ?? "unknown"}, text="${textOf(message.content)}"}`;
  }
  return `${message.role ?? "unknown"}{text="${textOf(message.content)}"}`;
}

function toolCallsOf(message: AgentMessage) {
  return Array.isArray(message.content)
    ? (message.content as Array<Record<string, unknown>>).filter((part) => part.type === "toolCall")
    : [];
}

function textOf(content: unknown) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return (content as Array<Record<string, unknown>>)
    .filter((part) => part.type === "text")
    .map((part) => String(part.text ?? ""))
    .join(" ")
    .trim();
}

function summarizeOvParts(parts: unknown) {
  if (!Array.isArray(parts)) return JSON.stringify(parts);
  return parts
    .map((part) => {
      const p = part as Record<string, unknown>;
      if (p.type === "text") return `text:${String(p.text ?? "").slice(0, 80)}`;
      if (p.type === "tool") {
        return `tool:${String(p.toolName)}:${String(p.toolCallId ?? "")}:${String(p.toolOutput ?? "").slice(0, 80)}`;
      }
      return String(p.type ?? "unknown");
    })
    .join(" | ");
}
