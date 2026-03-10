import { spawn } from "node:child_process";
import { tmpdir } from "node:os";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { Type } from "@sinclair/typebox";
import { memoryOpenVikingConfigSchema } from "./config.js";

import { OpenVikingClient, localClientCache, isMemoryUri } from "./client.js";
import type { FindResultItem } from "./client.js";
import {
  getCaptureDecision,
  isTranscriptLikeIngest,
  extractNewTurnTexts,
  extractLatestUserText,
} from "./text-utils.js";
import {
  clampScore,
  postProcessMemories,
  formatMemoryLines,
  trimForLog,
  toJsonLog,
  summarizeInjectionMemories,
  summarizeExtractedMemories,
  pickMemoriesForInjection,
} from "./memory-ranking.js";
import {
  IS_WIN,
  waitForHealth,
  withTimeout,
  quickRecallPrecheck,
  resolvePythonCommand,
  prepareLocalPort,
} from "./process-manager.js";

const memoryPlugin = {
  id: "memory-openviking",
  name: "Memory (OpenViking)",
  description: "OpenViking-backed long-term memory with auto-recall/capture",
  kind: "memory" as const,
  configSchema: memoryOpenVikingConfigSchema,

  register(api: OpenClawPluginApi) {
    const cfg = memoryOpenVikingConfigSchema.parse(api.pluginConfig);
    const localCacheKey = `${cfg.mode}:${cfg.baseUrl}:${cfg.configPath}:${cfg.apiKey}`;

    let clientPromise: Promise<OpenVikingClient>;
    let localProcess: ReturnType<typeof spawn> | null = null;
    let resolveLocalClient: ((c: OpenVikingClient) => void) | null = null;
    let rejectLocalClient: ((err: unknown) => void) | null = null;
    let localUnavailableReason: string | null = null;
    const autoRecallTimeoutMs = 5_000;

    const markLocalUnavailable = (reason: string, err?: unknown) => {
      if (!localUnavailableReason) {
        localUnavailableReason = reason;
        api.logger.warn(
          `memory-openviking: local mode marked unavailable (${reason})${err ? `: ${String(err)}` : ""}`,
        );
      }
      if (rejectLocalClient) {
        rejectLocalClient(
          err instanceof Error ? err : new Error(`memory-openviking unavailable: ${reason}`),
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
            `memory-openviking: memory_store invoked (textLength=${text?.length ?? 0}, sessionId=${sessionIdIn ?? "temp"})`,
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
                `memory-openviking: memory_store completed but extract returned 0 memories (sessionId=${sessionId}). ` +
                  "Check OpenViking server logs for embedding/extract errors (e.g. 401 API key, or extraction pipeline).",
              );
            } else {
              api.logger.info?.(`memory-openviking: memory_store extracted ${extracted.length} memories`);
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
            api.logger.warn(`memory-openviking: memory_store failed: ${String(err)}`);
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

    if (cfg.autoRecall || cfg.ingestReplyAssist) {
      api.on("before_agent_start", async (event: { messages?: unknown[]; prompt: string }) => {
        const queryText = extractLatestUserText(event.messages) || event.prompt.trim();
        if (!queryText) {
          return;
        }
        const prependContextParts: string[] = [];

        if (cfg.autoRecall && queryText.length >= 5) {
          const precheck = await quickRecallPrecheck(cfg.mode, cfg.baseUrl, cfg.port, localProcess);
          if (!precheck.ok) {
            api.logger.info?.(
              `memory-openviking: skipping auto-recall because precheck failed (${precheck.reason})`,
            );
          } else {
            try {
              const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
              // 同时检索 user 和 agent 两个位置的记忆
              const [userSettled, agentSettled] = await Promise.allSettled([
                getClient().then((client) =>
                  client.find(queryText, {
                    targetUri: "viking://user/memories",
                    limit: candidateLimit,
                    scoreThreshold: 0,
                  }),
                ),
                getClient().then((client) =>
                  client.find(queryText, {
                    targetUri: "viking://agent/memories",
                    limit: candidateLimit,
                    scoreThreshold: 0,
                  }),
                ),
              ]);
              const userResult = userSettled.status === "fulfilled" ? userSettled.value : { memories: [] };
              const agentResult = agentSettled.status === "fulfilled" ? agentSettled.value : { memories: [] };
              if (userSettled.status === "rejected") {
                api.logger.warn(`memory-openviking: user memories search failed: ${String(userSettled.reason)}`);
              }
              if (agentSettled.status === "rejected") {
                api.logger.warn(`memory-openviking: agent memories search failed: ${String(agentSettled.reason)}`);
              }
              // 合并两个位置的结果，去重
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
                // 对 level 2 节点读取正文，其余用 abstract
                const client = await getClient();
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
                api.logger.info?.(
                  `memory-openviking: injecting ${memories.length} memories into context`,
                );
                api.logger.info?.(
                  `memory-openviking: inject-detail ${toJsonLog({ count: memories.length, memories: summarizeInjectionMemories(memories) })}`,
                );
                prependContextParts.push(
                  "<relevant-memories>\nThe following OpenViking memories may be relevant:\n" +
                    `${memoryContext}\n` +
                  "</relevant-memories>",
                );
              }
            } catch (err) {
              api.logger.warn(`memory-openviking: auto-recall failed: ${String(err)}`);
            }
          }
        }

        if (cfg.ingestReplyAssist) {
          const decision = isTranscriptLikeIngest(queryText, {
            minSpeakerTurns: cfg.ingestReplyAssistMinSpeakerTurns,
            minChars: cfg.ingestReplyAssistMinChars,
          });
          if (decision.shouldAssist) {
            api.logger.info?.(
              `memory-openviking: ingest-reply-assist applied (reason=${decision.reason}, speakerTurns=${decision.speakerTurns}, chars=${decision.chars})`,
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
    }

    if (cfg.autoCapture) {
      let lastProcessedMsgCount = 0;

      api.on("agent_end", async (event: { success?: boolean; messages?: unknown[] }) => {
        if (!event.success || !event.messages || event.messages.length === 0) {
          api.logger.info(
            `memory-openviking: auto-capture skipped (success=${String(event.success)}, messages=${event.messages?.length ?? 0})`,
          );
          return;
        }
        try {
          const messages = event.messages;
          const { texts: newTexts, newCount } = extractNewTurnTexts(messages, lastProcessedMsgCount);
          lastProcessedMsgCount = messages.length;

          if (newTexts.length === 0) {
            api.logger.info("memory-openviking: auto-capture skipped (no new user/assistant messages)");
            return;
          }

          // 合并当前轮的 user+assistant 为一个文本块
          const turnText = newTexts.join("\n");

          // 对合并文本做 capture decision（主要检查长度和命令过滤）
          const decision = getCaptureDecision(turnText, cfg.captureMode, cfg.captureMaxLength);
          const preview = turnText.length > 80 ? `${turnText.slice(0, 80)}...` : turnText;
          api.logger.info(
            `memory-openviking: capture-check shouldCapture=${String(decision.shouldCapture)} reason=${decision.reason} newMsgCount=${newCount} text="${preview}"`,
          );
          if (!decision.shouldCapture) {
            api.logger.info("memory-openviking: auto-capture skipped (capture decision rejected)");
            return;
          }

          const c = await getClient();
          const sessionId = await c.createSession();
          try {
            await c.addSessionMessage(sessionId, "user", decision.normalizedText);
            // Force server to read session so storage (e.g. AGFS) sees the written messages before extract
            await c.getSession(sessionId).catch(() => ({}));
            const extracted = await c.extractSessionMemories(sessionId);
            api.logger.info(
              `memory-openviking: auto-captured ${newCount} new messages, extracted ${extracted.length} memories`,
            );
            api.logger.info(
              `memory-openviking: capture-detail ${toJsonLog({
                capturedCount: newCount,
                captured: [trimForLog(turnText, 260)],
                extractedCount: extracted.length,
                extracted: summarizeExtractedMemories(extracted),
              })}`,
            );
            if (extracted.length === 0) {
              api.logger.warn(
                "memory-openviking: auto-capture completed but extract returned 0 memories. Check OpenViking server logs for embedding/extract errors.",
              );
            }
          } finally {
            await c.deleteSession(sessionId).catch(() => {});
          }
        } catch (err) {
          api.logger.warn(`memory-openviking: auto-capture failed: ${String(err)}`);
        }
      });
    }

    api.registerService({
      id: "memory-openviking",
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
          child.on("error", (err: Error) => api.logger.warn(`memory-openviking: local server error: ${String(err)}`));
          child.stderr?.on("data", (chunk: Buffer) => {
            const s = String(chunk).trim();
            if (s) stderrChunks.push(s);
            api.logger.debug?.(`[openviking] ${s}`);
          });
          child.on("exit", (code: number | null, signal: string | null) => {
            if (localProcess === child && (code != null && code !== 0 || signal)) {
              const out = stderrChunks.length ? `\n[openviking stderr]\n${stderrChunks.join("\n")}` : "";
              api.logger.warn(`memory-openviking: subprocess exited (code=${code}, signal=${signal})${out}`);
            }
          });
          try {
            await waitForHealth(baseUrl, timeoutMs, intervalMs);
            const client = new OpenVikingClient(baseUrl, cfg.apiKey, cfg.agentId, cfg.timeoutMs);
            localClientCache.set(localCacheKey, { client, process: child });
            resolveLocalClient(client);
            rejectLocalClient = null;
            api.logger.info(
              `memory-openviking: local server started (${baseUrl}, config: ${cfg.configPath})`,
            );
          } catch (err) {
            localProcess = null;
            child.kill("SIGTERM");
            markLocalUnavailable("startup failed", err);
            if (stderrChunks.length) {
              api.logger.warn(
                `memory-openviking: startup failed (health check timeout or error). OpenViking stderr:\n${stderrChunks.join("\n")}`,
              );
            }
            throw err;
          }
        } else {
          await (await getClient()).healthCheck().catch(() => {});
          api.logger.info(
            `memory-openviking: initialized (url: ${cfg.baseUrl}, targetUri: ${cfg.targetUri}, search: hybrid endpoint)`,
          );
        }
      },
      stop: () => {
        if (localProcess) {
          localProcess.kill("SIGTERM");
          localClientCache.delete(localCacheKey);
          localProcess = null;
          api.logger.info("memory-openviking: local server stopped");
        } else {
          api.logger.info("memory-openviking: stopped");
        }
      },
    });
  },
};

export default memoryPlugin;
