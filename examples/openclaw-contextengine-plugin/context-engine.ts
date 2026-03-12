import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { parseConfig } from "./config.js";
import type { OpenVikingClient } from "./client.js";
import { buildSystemPromptAddition, buildSimulatedToolResultInjection, truncateToMaxChars } from "./injection.js";
import { toBatchPayload, writeBatchAndCommit } from "./ingestion.js";
import { classifyFallback } from "./fallback.js";
import { buildFallbackTelemetry } from "./telemetry.js";
import { buildTurnQuery, filterAndRank } from "./retrieval.js";
import {
  buildOvCliGuidance,
  buildSkillMemoryAugmentation,
  buildToolMemoryHints,
} from "./skill-tool-memory.js";
import type { ContextEngineOpenVikingPluginConfig } from "./types.js";

const RETRIEVAL_LIMIT = 10;
const MAX_RETRIEVAL_TEXT_CHARS = 2000;
const PROFILE_MEMORY_LIMIT = 5;
const PROFILE_MEMORY_QUERY = "user profile summary";
const SIMULATED_RETRIEVAL_TOOL_NAME = "search_memories";
const APPROX_CHARS_PER_TOKEN = 4;

type EngineDeps = {
  config: unknown;
  client: OpenVikingClient;
};

type AssembleParams = {
  sessionId: string;
  messages: Array<{ role?: string; content?: unknown }>;
  tokenBudget?: number;
};

type CompactParams = {
  sessionId: string;
  sessionFile: string;
  tokenBudget?: number;
  runtimeContext?: {
    messages?: Array<{ role?: string; content?: unknown }>;
  };
};

function buildSimulatedRetrievalMessages(params: {
  baseMessages: Array<{ role?: string; content?: unknown }>;
  retrievalText: string;
  query: string;
  scoreThreshold: number;
  limit: number;
  targetUri: string;
  sessionId: string;
}): Array<{ role?: string; content?: unknown }> {
  const sessionKey = params.sessionId.replace(/[^a-zA-Z0-9_-]/g, "_") || "session";
  const toolCallId = `ov_retrieval_${sessionKey}_${params.baseMessages.length}`;
  const assistantToolCall = {
    role: "assistant",
    content: [
      {
        type: "toolCall",
        id: toolCallId,
        name: SIMULATED_RETRIEVAL_TOOL_NAME,
        input: {
          query: params.query,
          limit: params.limit,
          scoreThreshold: params.scoreThreshold,
          targetUri: params.targetUri,
        },
      },
    ],
  };
  const toolResult = {
    role: "toolResult",
    toolCallId,
    toolName: SIMULATED_RETRIEVAL_TOOL_NAME,
    content: [{ type: "text", text: params.retrievalText }],
    isError: false,
  };
  return [...params.baseMessages, assistantToolCall, toolResult];
}

function isParsedConfig(value: unknown): value is ContextEngineOpenVikingPluginConfig {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const maybe = value as Partial<ContextEngineOpenVikingPluginConfig>;
  return (
    (maybe.mode === "local" || maybe.mode === "remote") &&
    typeof maybe.connection?.baseUrl === "string" &&
    typeof maybe.connection?.timeoutMs === "number" &&
    typeof maybe.connection?.apiKey === "string" &&
    typeof maybe.connection?.agentId === "string" &&
    typeof maybe.retrieval?.enabled === "boolean" &&
    typeof maybe.retrieval?.lastNUserMessages === "number" &&
    typeof maybe.retrieval?.targetUri === "string" &&
    (maybe.retrieval?.injectMode === "simulated_tool_result" || maybe.retrieval?.injectMode === "text") &&
    typeof maybe.profileInjection?.enabled === "boolean" &&
    typeof maybe.profileInjection?.qualityGateMinScore === "number" &&
    typeof maybe.profileInjection?.maxChars === "number" &&
    (maybe.ingestion?.writeMode === "compact_batch" || maybe.ingestion?.writeMode === "after_turn_batch") &&
    typeof maybe.ingestion?.maxBatchMessages === "number"
  );
}

export class OpenVikingContextEngine {
  readonly info = {
    id: "contextengine-openviking",
    name: "OpenViking Context Engine",
  };

  private readonly config: ContextEngineOpenVikingPluginConfig;
  private readonly client: OpenVikingClient;
  private sessionProfile = "";

  constructor(deps: EngineDeps) {
    this.config = isParsedConfig(deps.config) ? deps.config : parseConfig(deps.config);
    this.client = deps.client;
  }

  async bootstrap(params: {
    sessionId: string;
    sessionFile: string;
  }): Promise<{ bootstrapped: boolean; importedMessages: number }> {
    if (!this.config.profileInjection.enabled) {
      this.sessionProfile = "";
      return { bootstrapped: true, importedMessages: 0 };
    }

    const profilePath = join(dirname(dirname(params.sessionFile)), "profile.md");
    const profileSections: string[] = [];
    let importedMessages = 0;

    const profileMd = await readFile(profilePath, "utf-8").catch(() => "");
    if (profileMd.trim().length > 0) {
      profileSections.push(`profile.md:\n${profileMd.trim()}`);
    }

    try {
      const findResult = await this.client.find(PROFILE_MEMORY_QUERY, {
        targetUri: "viking://user/memories",
        limit: PROFILE_MEMORY_LIMIT,
        scoreThreshold: this.config.profileInjection.qualityGateMinScore,
      });
      const memories = filterAndRank(
        findResult.memories ?? [],
        this.config.profileInjection.qualityGateMinScore,
        PROFILE_MEMORY_LIMIT,
      );
      const memoryLines = memories
        .map((memory) => memory.content?.trim())
        .filter((content): content is string => typeof content === "string" && content.length > 0);
      importedMessages = memoryLines.length;
      if (memoryLines.length > 0) {
        profileSections.push(`High-confidence memory summary:\n${memoryLines.join("\n")}`);
      }
    } catch {
      // Profile enrichment is best-effort and must not block conversation startup.
    }

    this.sessionProfile = truncateToMaxChars(
      profileSections.join("\n\n"),
      this.config.profileInjection.maxChars,
    );
    return { bootstrapped: true, importedMessages };
  }

  async ingest(): Promise<{ ingested: boolean }> {
    return { ingested: true };
  }

  async afterTurn(): Promise<void> {}

  async assemble(params: AssembleParams): Promise<{
    messages: AssembleParams["messages"];
    estimatedTokens: number;
    systemPromptAddition?: string;
  }> {
    const query = buildTurnQuery(params.messages, this.config.retrieval.lastNUserMessages, {
      skipGreeting: this.config.retrieval.skipGreeting,
      minQueryChars: this.config.retrieval.minQueryChars,
    });

    let retrieved: Array<{ uri: string; score?: number; content?: string; level?: number }> = [];
    let fallbackTelemetryText = "";
    if (this.config.retrieval.enabled && query) {
      try {
        const result = await this.client.find(query, {
          scoreThreshold: this.config.retrieval.scoreThreshold,
          limit: RETRIEVAL_LIMIT,
          targetUri: this.config.retrieval.targetUri,
        });
        retrieved = filterAndRank(result.memories ?? [], this.config.retrieval.scoreThreshold, RETRIEVAL_LIMIT);
      } catch (error) {
        const fallbackKind = classifyFallback(error);
        const telemetry = buildFallbackTelemetry({ fallbackKind, error });
        fallbackTelemetryText = JSON.stringify(telemetry);
        retrieved = [];
      }
    }

    const retrievalTextForSimulatedToolResult =
      this.config.retrieval.injectMode === "simulated_tool_result"
        ? buildSimulatedToolResultInjection(retrieved)
        : "";
    const retrievalTextForPrompt =
      this.config.retrieval.injectMode === "text"
        ? truncateToMaxChars(
            retrieved
              .map((m) => m.content)
              .filter((s): s is string => typeof s === "string" && s.length > 0)
              .join("\n"),
            MAX_RETRIEVAL_TEXT_CHARS,
          )
        : "";

    const toolHints = buildToolMemoryHints(
      ["commit_memory", "search_memories"],
      "context_assembly",
      params.messages,
    );
    const skillHints = buildSkillMemoryAugmentation(
      ["superpowers:test-driven-development"],
      "context_assembly",
      params.messages,
    );
    const ovGuidance = buildOvCliGuidance({
      baseUrl: this.client.baseUrl,
      fallbackNote: "If OV is unavailable, continue without retrieval.",
    });

    const toolMemorySections = [toolHints, retrievalTextForPrompt, fallbackTelemetryText].filter(
      (s) => s.length > 0,
    );
    const profileBlock = [this.sessionProfile, skillHints].filter((part) => part.length > 0).join("\n\n");
    const systemPromptAddition = buildSystemPromptAddition({
      profile: profileBlock,
      toolMemory: toolMemorySections.join("\n"),
      ovCliGuidance: ovGuidance,
    });

    const messages =
      this.config.retrieval.enabled &&
      this.config.retrieval.injectMode === "simulated_tool_result" &&
      query.length > 0 &&
      fallbackTelemetryText.length === 0
        ? buildSimulatedRetrievalMessages({
            baseMessages: params.messages,
            retrievalText: retrievalTextForSimulatedToolResult,
            query,
            scoreThreshold: this.config.retrieval.scoreThreshold,
            limit: RETRIEVAL_LIMIT,
            targetUri: this.config.retrieval.targetUri,
            sessionId: params.sessionId,
          })
        : params.messages;

    const messagesJson = JSON.stringify(messages);
    const estimatedTokens = Math.ceil(messagesJson.length / APPROX_CHARS_PER_TOKEN);
    return {
      messages,
      estimatedTokens,
      systemPromptAddition,
    };
  }

  async compact(params: CompactParams): Promise<{
    ok: boolean;
    compacted: boolean;
    reason?: string;
    result?: { tokensBefore: number; tokensAfter?: number; details?: unknown };
  }> {
    const messages = params.runtimeContext?.messages ?? [];
    const payload = toBatchPayload({
      messages,
      includeSystemPrompt: true,
      includeToolCalls: true,
      maxBatchMessages: this.config.ingestion.maxBatchMessages,
      dedupeWindow: 5,
    });

    if (payload.length === 0) {
      return { ok: true, compacted: false, reason: "no_messages" };
    }

    const out = await writeBatchAndCommit(this.client, payload);
    return {
      ok: true,
      compacted: true,
      result: {
        tokensBefore: JSON.stringify(messages).length,
        tokensAfter: JSON.stringify(payload).length,
        details: out,
      },
    };
  }
}
