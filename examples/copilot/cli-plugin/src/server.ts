import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import {
  createDebugLogger,
  loadConfig as defaultLoadConfig,
  OVClient,
  type LoadConfigOptions,
  type OVResult,
  type OVTurn,
  type PluginConfig,
  type RecallHit,
  type RecallOptions,
  type ReadOptions,
  type CommitOptions,
} from "@openviking/copilot-shared";
import { z } from "zod/v4";

const SERVER_NAME = "openviking-copilot-mcp";
const DEFAULT_VERSION = "0.0.0";

export interface OpenVikingToolClient {
  health(): Promise<OVResult<unknown>>;
  recall(query: string, opts: RecallOptions): Promise<OVResult<RecallHit[]>>;
  read(uri: string, opts?: ReadOptions): Promise<OVResult<string>>;
  appendTurns(sessionId: string, turns: OVTurn[]): Promise<OVResult<unknown>>;
  commit(sessionId: string, opts?: CommitOptions): Promise<OVResult<unknown>>;
  forget(uri: string, opts?: { recursive?: boolean }): Promise<OVResult<unknown>>;
}

export interface OpenVikingToolDeps {
  client: OpenVikingToolClient;
  config?: Pick<PluginConfig, "recallLimit" | "scoreThreshold">;
}

export interface CreateOpenVikingMcpServerOptions extends OpenVikingToolDeps {
  version?: string;
}

export interface RunStdioMcpServerOptions {
  version?: string;
  loadConfig?: (opts: LoadConfigOptions) => PluginConfig;
  createClient?: (cfg: PluginConfig) => OpenVikingToolClient;
  createTransport?: () => Transport;
}

export function createOpenVikingMcpServer(opts: CreateOpenVikingMcpServerOptions): McpServer {
  const server = new McpServer({ name: SERVER_NAME, version: opts.version ?? DEFAULT_VERSION });
  registerOpenVikingTools(server, opts);
  return server;
}

export function registerOpenVikingTools(server: McpServer, deps: OpenVikingToolDeps): void {
  server.registerTool(
    "openviking_health",
    {
      title: "OpenViking health",
      description: "Check connectivity to the configured OpenViking server.",
      inputSchema: {},
    },
    async () => runTool(async () => fromOVResult(await deps.client.health())),
  );

  server.registerTool(
    "openviking_search",
    {
      title: "OpenViking search",
      description: "Search OpenViking memories, skills, resources, and related context.",
      inputSchema: {
        query: z.string().min(1),
        limit: z.number().int().positive().optional(),
        targetUri: z.string().min(1).optional(),
        scoreThreshold: z.number().min(0).max(1).optional(),
        sessionId: z.string().min(1).optional(),
      },
    },
    async (args) => runTool(async () => {
      const res = await deps.client.recall(args.query, {
        limit: args.limit ?? deps.config?.recallLimit ?? 6,
        sessionId: args.sessionId ?? "",
        targetUri: args.targetUri,
        scoreThreshold: args.scoreThreshold ?? deps.config?.scoreThreshold ?? 0.35,
      });
      return fromOVResult(res);
    }),
  );

  server.registerTool(
    "openviking_read",
    {
      title: "OpenViking read",
      description: "Read OpenViking content by URI.",
      inputSchema: {
        uri: z.string().min(1),
        offset: z.number().int().nonnegative().optional(),
        limit: z.number().int().positive().optional(),
      },
    },
    async (args) => runTool(async () => fromOVResult(
      await deps.client.read(args.uri, { offset: args.offset, limit: args.limit }),
      (value) => value,
    )),
  );

  server.registerTool(
    "openviking_store",
    {
      title: "OpenViking store",
      description: "Append one text turn to an OpenViking session and optionally commit it.",
      inputSchema: {
        sessionId: z.string().min(1),
        content: z.string().min(1),
        role: z.enum(["user", "assistant"]).optional(),
        commit: z.boolean().optional(),
      },
    },
    async (args) => runTool(async () => {
      const append = await deps.client.appendTurns(args.sessionId, [{
        role: args.role ?? "user",
        content: args.content,
      }]);
      if (!append.ok) return fromOVResult(append);

      if (args.commit === true) {
        const commit = await deps.client.commit(args.sessionId);
        if (!commit.ok) return fromOVResult(commit);
        return textJson({ append: append.value, commit: commit.value });
      }
      return textJson({ append: append.value });
    }),
  );

  server.registerTool(
    "openviking_forget",
    {
      title: "OpenViking forget",
      description: "Delete an OpenViking URI, optionally recursively.",
      inputSchema: {
        uri: z.string().min(1),
        recursive: z.boolean().optional(),
      },
    },
    async (args) => runTool(async () => fromOVResult(
      await deps.client.forget(args.uri, { recursive: args.recursive }),
    )),
  );
}

export async function runStdioMcpServer(opts: RunStdioMcpServerOptions = {}): Promise<void> {
  const loadConfig = opts.loadConfig ?? defaultLoadConfig;
  const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
  const client = opts.createClient
    ? opts.createClient(cfg)
    : new OVClient(cfg, { logger: createDebugLogger(cfg, { scope: "copilot-cli-mcp" }) });
  const server = createOpenVikingMcpServer({ client, config: cfg, version: opts.version });
  const transport = opts.createTransport ? opts.createTransport() : new StdioServerTransport();
  await server.connect(transport);
}

async function runTool(cb: () => Promise<CallToolResult>): Promise<CallToolResult> {
  try {
    return await cb();
  } catch (err) {
    return toolError(errorMessage(err));
  }
}

function fromOVResult<T>(res: OVResult<T>, format: (value: T) => string = jsonString): CallToolResult {
  if (!res.ok) return toolError(`${res.error.status ? `HTTP ${res.error.status}: ` : ""}${res.error.message}`);
  return { content: [{ type: "text", text: format(res.value) }] };
}

function textJson(value: unknown): CallToolResult {
  return { content: [{ type: "text", text: jsonString(value) }] };
}

function toolError(message: string): CallToolResult {
  return { isError: true, content: [{ type: "text", text: message }] };
}

function jsonString(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
