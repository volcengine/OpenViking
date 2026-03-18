import { spawn } from "node:child_process";
import { tmpdir } from "node:os";

import { Type } from "@sinclair/typebox";
import { memoryOpenVikingConfigSchema } from "./config.js";

import { OpenVikingClient, localClientCache, isMemoryUri } from "./client.js";
import type { FindResultItem } from "./client.js";
import {
  isTranscriptLikeIngest,
  extractLatestUserText,
} from "./text-utils.js";
import {
  clampScore,
  postProcessMemories,
  formatMemoryLines,
  toJsonLog,
  summarizeInjectionMemories,
  pickMemoriesForInjection,
} from "./memory-ranking.js";
import {
  IS_WIN,
  waitForHealth,
  quickRecallPrecheck,
  withTimeout,
  resolvePythonCommand,
  prepareLocalPort,
} from "./process-manager.js";
import { createMemoryOpenVikingContextEngine } from "./context-engine.js";

type PluginLogger = {
  debug?: (message: string) => void;
  info: (message: string) => void;
  warn: (message: string) => void;
  error: (message: string) => void;
};

type HookAgentContext = {
  agentId?: string;
  sessionId?: string;
  sessionKey?: string;
};

type OpenClawPluginApi = {
  pluginConfig?: unknown;
  logger: PluginLogger;
  registerTool: (
    tool: {
      name: string;
      label: string;
      description: string;
      parameters: unknown;
      execute: (_toolCallId: string, params: Record<string, unknown>) => Promise<unknown>;
    },
    opts?: { name?: string; names?: string[] },
  ) => void;
  registerService: (service: {
    id: string;
    start: (ctx?: unknown) => void | Promise<void>;
    stop?: (ctx?: unknown) => void | Promise<void>;
  }) => void;
  registerContextEngine?: (id: string, factory: () => unknown) => void;
  on: (
    hookName: string,
    handler: (event: unknown, ctx?: HookAgentContext) => unknown,
    opts?: { priority?: number },
  ) => void;
};

const MAX_OPENVIKING_STDERR_LINES = 200;
const MAX_OPENVIKING_STDERR_CHARS = 256_000;

const contextEnginePlugin = {
  id: "openviking",
  name: "Context Engine (OpenViking)",
  description: "OpenViking-backed context-engine memory with auto-recall/capture",
  kind: "context-engine" as const,
  configSchema: memoryOpenVikingConfigSchema,

  register(api: OpenClawPluginApi) {
    const cfg = memoryOpenVikingConfigSchema.parse(api.pluginConfig);
    const localCacheKey = `${cfg.mode}:${cfg.baseUrl}:${cfg.configPath}:${cfg.apiKey}`;

    let clientPromise: Promise<OpenVikingClient>;
    let localProcess: ReturnType<typeof spawn> | null = null;
    let resolveLocalClient: ((c: OpenVikingClient) => void) | null = null;
    let rejectLocalClient: ((err: unknown) => void) | null = null;
    let localUnavailableReason: string | null = null;
    const markLocalUnavailable = (reason: string, err?: unknown) => {
      if (!localUnavailableReason) {
        localUnavailableReason = reason;
        api.logger.warn(
          `openviking: local mode marked unavailable (${reason})${err ? `: ${String(err)}` : ""}`,
        );
      }
      if (rejectLocalClient) {
        rejectLocalClient(
          err instanceof Error ? err : new Error(`openviking unavailable: ${reason}`),
        );
        rejectLocalClient = null;
      }
      resolveLocalClient = null;
    };

    if (cfg.mode === "local") {
      const cached = localClientCache.get(localCacheKey);
      if (cached) {
        localProcess = cached.process;
        clientPromise = Promise.resolve(cached.client);
      } else {
        clientPromise = new Promise<OpenVikingClient>((resolve, reject) => {
          resolveLocalClient = resolve;
          rejectLocalClient = reject;
        });
      }
    } else {
      clientPromise = Promise.resolve(new OpenVikingClient(cfg.baseUrl, cfg.apiKey, cfg.agentId, cfg.timeoutMs));
    }

    const getClient = (): Promise<OpenVikingClient> => clientPromise;

    api.registerTool(
      {
        name: "memory_recall",
        label: "Memory Recall (OpenViking)",
        description:
          "Search long-term memories from OpenViking. Use when you need past user preferences, facts, or decisions.",
        parameters: Type.Object({
          query: Type.String({ description: "Search query" }),
          limit: Type.Optional(
            Type.Number({ description: "Max results (default: plugin config)" }),
          ),
          scoreThreshold: Type.Optional(
            Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
          ),
          targetUri: Type.Optional(
            Type.String({ description: "Search scope URI (default: plugin config)" }),
          ),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          const { query } = params as { query: string };
          const limit =
            typeof (params as { limit?: number }).limit === "number"
              ? Math.max(1, Math.floor((params as { limit: number }).limit))
              : cfg.recallLimit;
          const scoreThreshold =
            typeof (params as { scoreThreshold?: number }).scoreThreshold === "number"
              ? Math.max(0, Math.min(1, (params as { scoreThreshold: number }).scoreThreshold))
              : cfg.recallScoreThreshold;
          const targetUri =
            typeof (params as { targetUri?: string }).targetUri === "string"
              ? (params as { targetUri: string }).targetUri
              : undefined;
          const requestLimit = Math.max(limit * 4, 20);

          let result;
          if (targetUri) {
            // 如果指定了目标 URI，只检索该位置
            result = await (await getClient()).find(query, {
              targetUri,
              limit: requestLimit,
              scoreThreshold: 0,
            });
          } else {
            // 默认同时检索 user 和 agent 两个位置的记忆
            const [userSettled, agentSettled] = await Promise.allSettled([
              (await getClient()).find(query, {
                targetUri: "viking://user/memories",
                limit: requestLimit,
                scoreThreshold: 0,
              }),
              (await getClient()).find(query, {
                targetUri: "viking://agent/memories",
                limit: requestLimit,
                scoreThreshold: 0,
              }),
            ]);
            const userResult = userSettled.status === "fulfilled" ? userSettled.value : { memories: [] };
            const agentResult = agentSettled.status === "fulfilled" ? agentSettled.value : { memories: [] };
            // 合并两个位置的结果，去重
            const allMemories = [...(userResult.memories ?? []), ...(agentResult.memories ?? [])];
            const uniqueMemories = allMemories.filter((memory, index, self) =>
              index === self.findIndex((m) => m.uri === memory.uri)
            );
            const leafOnly = uniqueMemories.filter((m) => m.level === 2);
            result = {
              memories: leafOnly,
              total: leafOnly.length,
            };
          }

          const memories = postProcessMemories(result.memories ?? [], {
            limit,
            scoreThreshold,
          });
          if (memories.length === 0) {
            return {
              content: [{ type: "text", text: "No relevant OpenViking memories found." }],
              details: { count: 0, total: result.total ?? 0, scoreThreshold },
            };
          }
          return {
            content: [
              {
                type: "text",
                text: `Found ${memories.length} memories:\n\n${formatMemoryLines(memories)}`,
              },
            ],
            details: {
              count: memories.length,
              memories,
              total: result.total ?? memories.length,
              scoreThreshold,
              requestLimit,
            },
          };
        },
      },
      { name: "memory_recall" },
    );

    api.registerTool(
      {
        name: "memory_store",
        label: "Memory Store (OpenViking)",
        description:
          "Store text in OpenViking memory pipeline by writing to a session and running memory extraction.",
        parameters: Type.Object({
          text: Type.String({ description: "Information to store as memory source text" }),
          role: Type.Optional(Type.String({ description: "Session role, default user" })),
          sessionId: Type.Optional(Type.String({ description: "Existing OpenViking session ID" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          const { text } = params as { text: string };
          const role =
            typeof (params as { role?: string }).role === "string"
              ? (params as { role: string }).role
              : "user";
          const sessionIdIn = (params as { sessionId?: string }).sessionId;

          api.logger.info?.(
            `openviking: memory_store invoked (textLength=${text?.length ?? 0}, sessionId=${sessionIdIn ?? "temp"})`,
          );

          let sessionId = sessionIdIn;
          let createdTempSession = false;
          try {
            const c = await getClient();
            if (!sessionId) {
              sessionId = await c.createSession();
              createdTempSession = true;
            }
            await c.addSessionMessage(sessionId, role, text);
            const extracted = await c.extractSessionMemories(sessionId);
            if (extracted.length === 0) {
              api.logger.warn(
                `openviking: memory_store completed but extract returned 0 memories (sessionId=${sessionId}). ` +
                  "Check OpenViking server logs for embedding/extract errors (e.g. 401 API key, or extraction pipeline).",
              );
            } else {
              api.logger.info?.(`openviking: memory_store extracted ${extracted.length} memories`);
            }
            return {
              content: [
                {
                  type: "text",
                  text: `Stored in OpenViking session ${sessionId} and extracted ${extracted.length} memories.`,
                },
              ],
              details: { action: "stored", sessionId, extractedCount: extracted.length, extracted },
            };
          } catch (err) {
            api.logger.warn(`openviking: memory_store failed: ${String(err)}`);
            throw err;
          } finally {
            if (createdTempSession && sessionId) {
              const c = await getClient().catch(() => null);
              if (c) await c.deleteSession(sessionId!).catch(() => {});
            }
          }
        },
      },
      { name: "memory_store" },
    );

    api.registerTool(
      {
        name: "memory_forget",
        label: "Memory Forget (OpenViking)",
        description:
          "Forget memory by URI, or search then delete when a strong single match is found.",
        parameters: Type.Object({
          uri: Type.Optional(Type.String({ description: "Exact memory URI to delete" })),
          query: Type.Optional(Type.String({ description: "Search query to find memory URI" })),
          targetUri: Type.Optional(
            Type.String({ description: "Search scope URI (default: plugin config)" }),
          ),
          limit: Type.Optional(Type.Number({ description: "Search limit (default: 5)" })),
          scoreThreshold: Type.Optional(
            Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
          ),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          const uri = (params as { uri?: string }).uri;
          if (uri) {
            if (!isMemoryUri(uri)) {
              return {
                content: [{ type: "text", text: `Refusing to delete non-memory URI: ${uri}` }],
                details: { action: "rejected", uri },
              };
            }
            await (await getClient()).deleteUri(uri);
            return {
              content: [{ type: "text", text: `Forgotten: ${uri}` }],
              details: { action: "deleted", uri },
            };
          }

          const query = (params as { query?: string }).query;
          if (!query) {
            return {
              content: [{ type: "text", text: "Provide uri or query." }],
              details: { error: "missing_param" },
            };
          }

          const limit =
            typeof (params as { limit?: number }).limit === "number"
              ? Math.max(1, Math.floor((params as { limit: number }).limit))
              : 5;
          const scoreThreshold =
            typeof (params as { scoreThreshold?: number }).scoreThreshold === "number"
              ? Math.max(0, Math.min(1, (params as { scoreThreshold: number }).scoreThreshold))
              : cfg.recallScoreThreshold;
          const targetUri =
            typeof (params as { targetUri?: string }).targetUri === "string"
              ? (params as { targetUri: string }).targetUri
              : cfg.targetUri;
          const requestLimit = Math.max(limit * 4, 20);

          const result = await (await getClient()).find(query, {
            targetUri,
            limit: requestLimit,
            scoreThreshold: 0,
          });
          const candidates = postProcessMemories(result.memories ?? [], {
            limit: requestLimit,
            scoreThreshold,
            leafOnly: true,
          }).filter((item) => isMemoryUri(item.uri));
          if (candidates.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text: "No matching leaf memory candidates found. Try a more specific query.",
                },
              ],
              details: { action: "none", scoreThreshold },
            };
          }
          const top = candidates[0];
          if (candidates.length === 1 && clampScore(top.score) >= 0.85) {
            await (await getClient()).deleteUri(top.uri);
            return {
              content: [{ type: "text", text: `Forgotten: ${top.uri}` }],
              details: { action: "deleted", uri: top.uri, score: top.score ?? 0 },
            };
          }

          const list = candidates
            .map((item) => `- ${item.uri} (${(clampScore(item.score) * 100).toFixed(0)}%)`)
            .join("\n");

          return {
            content: [
              {
                type: "text",
                text: `Found ${candidates.length} candidates. Specify uri:\n${list}`,
              },
            ],
            details: { action: "candidates", candidates, scoreThreshold, requestLimit },
          };
        },
      },
      { name: "memory_forget" },
    );
    const sessionAgentIds = new Map<string, string>();
    const rememberSessionAgentId = (ctx: {
      agentId?: string;
      sessionId?: string;
      sessionKey?: string;
    }) => {
      if (!ctx?.agentId) {
        return;
      }
      if (ctx.sessionId) {
        sessionAgentIds.set(ctx.sessionId, ctx.agentId);
      }
      if (ctx.sessionKey) {
        sessionAgentIds.set(ctx.sessionKey, ctx.agentId);
      }
    };
    const resolveAgentId = (sessionId: string): string =>
      sessionAgentIds.get(sessionId) ?? cfg.agentId;

    api.on("session_start", async (_event: unknown, ctx?: HookAgentContext) => {
      rememberSessionAgentId(ctx ?? {});
    });
    api.on("session_end", async (_event: unknown, ctx?: HookAgentContext) => {
      rememberSessionAgentId(ctx ?? {});
    });
    api.on("before_prompt_build", async (event: unknown, ctx?: HookAgentContext) => {
      rememberSessionAgentId(ctx ?? {});

      const hookSessionId = ctx?.sessionId ?? ctx?.sessionKey ?? "";
      const resolvedAgentId = resolveAgentId(hookSessionId);
      let client: OpenVikingClient;
      try {
        client = await withTimeout(
          getClient(),
          5000,
          "openviking: client initialization timeout (OpenViking service not ready yet)"
        );
      } catch (err) {
        api.logger.warn?.(`openviking: failed to get client: ${String(err)}`);
        return;
      }
      if (resolvedAgentId && client.getAgentId() !== resolvedAgentId) {
        client.setAgentId(resolvedAgentId);
        api.logger.info(`openviking: switched to agentId=${resolvedAgentId} for before_prompt_build`);
      }

      const eventObj = (event ?? {}) as { messages?: unknown[]; prompt?: string };
      const queryText =
        extractLatestUserText(eventObj.messages) ||
        (typeof eventObj.prompt === "string" ? eventObj.prompt.trim() : "");
      if (!queryText) {
        return;
      }

      const prependContextParts: string[] = [];

      if (cfg.autoRecall && queryText.length >= 5) {
        const precheck = await quickRecallPrecheck(cfg.mode, cfg.baseUrl, cfg.port, localProcess);
        if (!precheck.ok) {
          api.logger.info(
            `openviking: skipping auto-recall because precheck failed (${precheck.reason})`,
          );
        } else {
          try {
            const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
            const [userSettled, agentSettled] = await Promise.allSettled([
              client.find(queryText, {
                targetUri: "viking://user/memories",
                limit: candidateLimit,
                scoreThreshold: 0,
              }),
              client.find(queryText, {
                targetUri: "viking://agent/memories",
                limit: candidateLimit,
                scoreThreshold: 0,
              }),
            ]);

            const userResult = userSettled.status === "fulfilled" ? userSettled.value : { memories: [] };
            const agentResult = agentSettled.status === "fulfilled" ? agentSettled.value : { memories: [] };
            if (userSettled.status === "rejected") {
              api.logger.warn(`openviking: user memories search failed: ${String(userSettled.reason)}`);
            }
            if (agentSettled.status === "rejected") {
              api.logger.warn(`openviking: agent memories search failed: ${String(agentSettled.reason)}`);
            }

            const allMemories = [...(userResult.memories ?? []), ...(agentResult.memories ?? [])];
            const uniqueMemories = allMemories.filter((memory, index, self) =>
              index === self.findIndex((m) => m.uri === memory.uri)
            );
            const leafOnly = uniqueMemories.filter((m) => m.level === 2);
            const processed = postProcessMemories(leafOnly, {
              limit: candidateLimit,
              scoreThreshold: cfg.recallScoreThreshold,
            });
            const memories = pickMemoriesForInjection(processed, cfg.recallLimit, queryText);

            if (memories.length > 0) {
              const memoryLines = await Promise.all(
                memories.map(async (item: FindResultItem) => {
                  if (item.level === 2) {
                    try {
                      const content = await client.read(item.uri);
                      if (content && typeof content === "string" && content.trim()) {
                        return `- [${item.category ?? "memory"}] ${content.trim()}`;
                      }
                    } catch {
                      // fallback to abstract
                    }
                  }
                  return `- [${item.category ?? "memory"}] ${item.abstract ?? item.uri}`;
                }),
              );
              const memoryContext = memoryLines.join("\n");
              api.logger.info(`openviking: injecting ${memories.length} memories into context`);
              api.logger.info(
                `openviking: inject-detail ${toJsonLog({ count: memories.length, memories: summarizeInjectionMemories(memories) })}`,
              );
              prependContextParts.push(
                "<relevant-memories>\nThe following OpenViking memories may be relevant:\n" +
                  `${memoryContext}\n` +
                "</relevant-memories>",
              );
            }
          } catch (err) {
            api.logger.warn(`openviking: auto-recall failed: ${String(err)}`);
          }
        }
      }

      if (cfg.ingestReplyAssist) {
        const decision = isTranscriptLikeIngest(queryText, {
          minSpeakerTurns: cfg.ingestReplyAssistMinSpeakerTurns,
          minChars: cfg.ingestReplyAssistMinChars,
        });
        if (decision.shouldAssist) {
          api.logger.info(
            `openviking: ingest-reply-assist applied (reason=${decision.reason}, speakerTurns=${decision.speakerTurns}, chars=${decision.chars})`,
          );
          prependContextParts.push(
            "<ingest-reply-assist>\n" +
              "The latest user input looks like a multi-speaker transcript used for memory ingestion.\n" +
              "Reply with 1-2 concise sentences to acknowledge or summarize key points.\n" +
              "Do not output NO_REPLY or an empty reply.\n" +
              "Do not fabricate facts beyond the provided transcript and recalled memories.\n" +
              "</ingest-reply-assist>",
          );
        }
      }

      if (prependContextParts.length > 0) {
        return {
          prependContext: prependContextParts.join("\n\n"),
        };
      }
    });
    api.on("agent_end", async (_event: unknown, ctx?: HookAgentContext) => {
      rememberSessionAgentId(ctx ?? {});
    });
    api.on("before_reset", async (_event: unknown, _ctx?: HookAgentContext) => {
      // Reserved hook registration for future memory flush/reset handling.
    });
    api.on("after_compaction", async (_event: unknown, _ctx?: HookAgentContext) => {
      // Reserved hook registration for future post-compaction memory integration.
    });

    if (typeof api.registerContextEngine === "function") {
      api.registerContextEngine(contextEnginePlugin.id, () =>
        createMemoryOpenVikingContextEngine({
          id: contextEnginePlugin.id,
          name: contextEnginePlugin.name,
          version: "0.1.0",
          cfg,
          logger: api.logger,
          getClient,
          resolveAgentId,
        }),
      );
      api.logger.info(
        "openviking: registered context-engine (before_prompt_build=auto-recall, afterTurn=auto-capture)",
      );
    } else {
      api.logger.warn(
        "openviking: registerContextEngine is unavailable; context-engine behavior will not run",
      );
    }

    api.registerService({
      id: "openviking",
      start: async () => {
        if (cfg.mode === "local" && resolveLocalClient) {
          const timeoutMs = 60_000;
          const intervalMs = 500;

          // Prepare port: kill stale OpenViking, or auto-find free port if occupied by others
          const actualPort = await prepareLocalPort(cfg.port, api.logger);
          const baseUrl = `http://127.0.0.1:${actualPort}`;

          const pythonCmd = resolvePythonCommand(api.logger);

          // Inherit system environment; optionally override Go/Python paths via env vars
          const pathSep = IS_WIN ? ";" : ":";
          const env = {
            ...process.env,
            PYTHONUNBUFFERED: "1",
            PYTHONWARNINGS: "ignore::RuntimeWarning",
            OPENVIKING_CONFIG_FILE: cfg.configPath,
            OPENVIKING_START_CONFIG: cfg.configPath,
            OPENVIKING_START_HOST: "127.0.0.1",
            OPENVIKING_START_PORT: String(actualPort),
            ...(process.env.OPENVIKING_GO_PATH && { PATH: `${process.env.OPENVIKING_GO_PATH}${pathSep}${process.env.PATH || ""}` }),
            ...(process.env.OPENVIKING_GOPATH && { GOPATH: process.env.OPENVIKING_GOPATH }),
            ...(process.env.OPENVIKING_GOPROXY && { GOPROXY: process.env.OPENVIKING_GOPROXY }),
          };
          // Run OpenViking server: use run_path on the module file to avoid RuntimeWarning from
          // "parent package import loads submodule before execution" (exit 3). Fallback to run_module with warning suppressed.
          const runpyCode = `import sys,os,warnings; warnings.filterwarnings('ignore', category=RuntimeWarning, message='.*sys.modules.*'); sys.argv=['openviking.server.bootstrap','--config',os.environ['OPENVIKING_START_CONFIG'],'--host',os.environ.get('OPENVIKING_START_HOST','127.0.0.1'),'--port',os.environ['OPENVIKING_START_PORT']]; import runpy, importlib.util; spec=importlib.util.find_spec('openviking.server.bootstrap'); (runpy.run_path(spec.origin, run_name='__main__') if spec and getattr(spec,'origin',None) else runpy.run_module('openviking.server.bootstrap', run_name='__main__', alter_sys=True))`;
          const child = spawn(
            pythonCmd,
            ["-c", runpyCode],
            { env, cwd: IS_WIN ? tmpdir() : "/tmp", stdio: ["ignore", "pipe", "pipe"] },
          );
          localProcess = child;
          const stderrChunks: string[] = [];
          let stderrCharCount = 0;
          let stderrDroppedChunks = 0;
          const pushStderrChunk = (chunk: string) => {
            if (!chunk) return;
            stderrChunks.push(chunk);
            stderrCharCount += chunk.length;
            while (
              stderrChunks.length > MAX_OPENVIKING_STDERR_LINES ||
              stderrCharCount > MAX_OPENVIKING_STDERR_CHARS
            ) {
              const dropped = stderrChunks.shift();
              if (!dropped) break;
              stderrCharCount -= dropped.length;
              stderrDroppedChunks += 1;
            }
          };
          const formatStderrOutput = () => {
            if (!stderrChunks.length && !stderrDroppedChunks) return "";
            const truncated =
              stderrDroppedChunks > 0
                ? `[truncated ${stderrDroppedChunks} earlier stderr chunk(s)]\n`
                : "";
            return `\n[openviking stderr]\n${truncated}${stderrChunks.join("\n")}`;
          };
          child.on("error", (err: Error) => api.logger.warn(`openviking: local server error: ${String(err)}`));
          child.stderr?.on("data", (chunk: Buffer) => {
            const s = String(chunk).trim();
            pushStderrChunk(s);
            api.logger.debug?.(`[openviking] ${s}`);
          });
          child.on("exit", (code: number | null, signal: string | null) => {
            if (localProcess === child) {
              localProcess = null;
              localClientCache.delete(localCacheKey);
            }
            if (code != null && code !== 0 || signal) {
              const out = formatStderrOutput();
              api.logger.warn(`openviking: subprocess exited (code=${code}, signal=${signal})${out}`);
            }
          });
          try {
            await waitForHealth(baseUrl, timeoutMs, intervalMs);
            const client = new OpenVikingClient(baseUrl, cfg.apiKey, cfg.agentId, cfg.timeoutMs);
            localClientCache.set(localCacheKey, { client, process: child });
            resolveLocalClient(client);
            rejectLocalClient = null;
            api.logger.info(
              `openviking: local server started (${baseUrl}, config: ${cfg.configPath})`,
            );
          } catch (err) {
            localProcess = null;
            child.kill("SIGTERM");
            markLocalUnavailable("startup failed", err);
            if (stderrChunks.length) {
              api.logger.warn(
                `openviking: startup failed (health check timeout or error).${formatStderrOutput()}`,
              );
            }
            throw err;
          }
        } else {
          await (await getClient()).healthCheck().catch(() => {});
          api.logger.info(
            `openviking: initialized (url: ${cfg.baseUrl}, targetUri: ${cfg.targetUri}, search: hybrid endpoint)`,
          );
        }
      },
      stop: () => {
        if (localProcess) {
          localProcess.kill("SIGTERM");
          localClientCache.delete(localCacheKey);
          localProcess = null;
          api.logger.info("openviking: local server stopped");
        } else {
          api.logger.info("openviking: stopped");
        }
      },
    });
  },
};

export default contextEnginePlugin;
