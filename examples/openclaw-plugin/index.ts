import { memoryOpenVikingConfigSchema } from "./config.js";
import { registerSetupCli } from "./commands/setup.js";
import { createOpenVikingBypassRuntime } from "./plugin/openviking-bypass-runtime.js";
import { createOpenVikingClientRuntime } from "./plugin/openviking-client-runtime.js";
import { createOpenVikingCommandDefinitions } from "./plugin/openviking-command-definitions.js";
import {
  parseAddResourceCommandArgs,
  parseAddSkillCommandArgs,
  parseOVSearchCommandArgs,
} from "./plugin/openviking-command-args.js";
import { createOpenVikingContextEngineRef } from "./plugin/openviking-context-engine-ref.js";
import { registerOpenVikingContextEngine } from "./plugin/openviking-context-engine-registration.js";
import { registerOpenVikingFeatureGatesMethod } from "./plugin/openviking-feature-gates.js";
import { createOpenVikingQueryConfigCommandHandler } from "./plugin/openviking-query-config-command.js";
import { createOpenVikingQueryRuntime } from "./plugin/openviking-query-runtime.js";
import { createOpenVikingRecallTraceRuntime } from "./plugin/openviking-recall-trace-runtime.js";
import { createOpenVikingToolRegistrationRuntime } from "./plugin/tool-registration.js";
import { registerOpenVikingCommands } from "./plugin/command-registration.js";
import { registerRecallTraceRoutes as registerRecallTraceRouteAdapters } from "./plugin/recall-trace-routes.js";
import { createOpenVikingService } from "./plugin/openviking-services.js";
import { registerOpenVikingArchiveTools } from "./plugin/openviking-archive-tools.js";
import { createOpenVikingImportRuntime } from "./plugin/openviking-import-runtime.js";
import { registerOpenVikingImportTools } from "./plugin/openviking-import-tools.js";
import { registerOpenVikingLifecycleHooks } from "./plugin/openviking-lifecycle-hooks.js";
import { registerOpenVikingMemoryRecallTools } from "./plugin/openviking-memory-recall-tools.js";
import { registerOpenVikingMemoryTools } from "./plugin/openviking-memory-tools.js";
import { registerOpenVikingQueryTools } from "./plugin/openviking-query-tools.js";
import { registerOpenVikingRecallTraceTools } from "./plugin/openviking-recall-trace-tools.js";
import { createOpenVikingRuntimeState } from "./plugin/openviking-runtime-state.js";
import { createOpenVikingSessionRoutingRuntime } from "./plugin/openviking-session-routing-runtime.js";
import { registerOpenVikingToolResultTools } from "./plugin/openviking-tool-result-tools.js";
import {
  boundTraceQuery,
  createMemoryStoreTempSessionId,
  createTraceId,
  extractToolSenderId,
  inferRecallResourceType,
  makeBypassedToolResult,
  previewText,
} from "./plugin/openviking-runtime-utils.js";
import type { CommandDefinition } from "./plugin/command-registration.js";

import type { HttpTransport } from "./adapters/http-transport.js";
import { formatMessageFaithful, toRoleId } from "./services/context-message-adapter.js";
import {
  clampScore,
  postProcessMemories,
  pickMemoriesForInjection,
} from "./memory-ranking.js";
import { withTimeout } from "./process-manager.js";
import {
  createMemoryOpenVikingContextEngine,
} from "./context-engine.js";
import {
  openClawSessionRefToOvStorageId,
  openClawSessionToOvStorageId,
} from "./routing/identity-routing.js";
import {
  buildMemoryLinesWithBudget,
} from "./auto-recall.js";
import {
  normalizeRecallResourceTypes as normalizeResourceTypes,
  resolveRecallSearchPlan,
} from "./registries/recall-resource-types.js";
import {
  normalizeRuntimeQueryParams,
} from "./query-config.js";

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

type ToolDefinition = {
  name: string;
  label: string;
  description: string;
  parameters: unknown;
  execute: (_toolCallId: string, params: Record<string, unknown>) => Promise<unknown>;
};

type ToolContext = {
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  senderId?: string;
};

type OpenClawPluginApi = {
  pluginConfig?: unknown;
  openVikingTransport?: HttpTransport;
  logger: PluginLogger;
  registerTool: {
    (tool: ToolDefinition, opts?: { name?: string; names?: string[] }): void;
    (
      factory: (ctx: ToolContext) => ToolDefinition,
      opts?: { name?: string; names?: string[] },
    ): void;
  };
  registerCommand?: (command: CommandDefinition) => void;
  registerService: (service: {
    id: string;
    start: (ctx?: unknown) => void | Promise<void>;
    stop?: (ctx?: unknown) => void | Promise<void>;
  }) => void;
  registerContextEngine?: (id: string, factory: () => unknown) => void;
  registerGatewayMethod?: (
    name: string,
    handler: (input: {
      params?: unknown;
      respond: (success: boolean, data: unknown) => void;
    }) => void | Promise<void>,
  ) => void;
  registerHttpRoute?: (route: {
    path: string;
    auth: "gateway" | "plugin";
    match?: "exact" | "prefix";
    handler: (req: { method?: string; url?: string }, res: {
      statusCode?: number;
      setHeader?: (name: string, value: string) => void;
      end?: (body?: string) => void;
    }) => Promise<boolean> | boolean;
  }) => void;
  registerCli?: (
    factory: (ctx: { program: unknown; workspaceDir?: string }) => void,
    opts?: { commands?: string[] },
  ) => void;
  on: (
    hookName: string,
    handler: (event: unknown, ctx?: HookAgentContext) => unknown,
    opts?: { priority?: number },
  ) => void;
};

type RecallTraceRouteAdapter = {
  registerRoute?: (route: {
    method: "GET";
    path: string;
    handler: (request?: { query?: Record<string, unknown>; params?: Record<string, unknown>; url?: string }) => Promise<unknown>;
  }) => void;
  registerHttpRoute?: OpenClawPluginApi["registerHttpRoute"];
};

const contextEnginePlugin = {
  id: "openviking",
  name: "Context Engine (OpenViking)",
  description: "OpenViking-backed context-engine memory with auto-recall/capture",
  kind: "context-engine" as const,
  configSchema: memoryOpenVikingConfigSchema,

  register(api: OpenClawPluginApi) {
    registerOpenVikingFeatureGatesMethod(api);

    const rawCfg =
      api.pluginConfig && typeof api.pluginConfig === "object" && !Array.isArray(api.pluginConfig)
        ? (api.pluginConfig as Record<string, unknown>)
        : {};

    if (rawCfg.mode && rawCfg.mode !== "remote") {
      api.logger.warn(
        `openviking: legacy local mode detected (mode="${String(rawCfg.mode)}"). ` +
          "Migrating to remote mode. Please run 'openclaw openviking setup' to configure the remote server.",
      );
      rawCfg.mode = "remote";
      delete rawCfg.localBinaryPath;
      delete rawCfg.localDataDir;
      delete rawCfg.localPort;
      delete rawCfg.autoStart;
    }

    let cfg: ReturnType<typeof memoryOpenVikingConfigSchema.parse>;
    try {
      cfg = memoryOpenVikingConfigSchema.parse(rawCfg);
    } catch (parseErr) {
      api.logger.warn(
        `openviking: config parse failed (${parseErr instanceof Error ? parseErr.message : String(parseErr)}). ` +
          "Plugin loaded in setup-only mode. Run: openclaw openviking setup",
      );
      registerSetupCli(api);
      return;
    }

    const { isBypassedSession } = createOpenVikingBypassRuntime({ cfg });
	    const { getClient, verboseRoutingInfo } = createOpenVikingClientRuntime({
	      cfg,
	      rawPeerPrefix: rawCfg.peer_prefix,
	      logger: api.logger,
	      transport: api.openVikingTransport,
	    });
    const { registerOpenVikingTool } = createOpenVikingToolRegistrationRuntime({
      api: api as { registerTool: (toolOrFactory: unknown, opts: { name: string }) => void },
      cfg,
      logger: api.logger,
    });

    const { queryConfigStore, traceRecorder } = createOpenVikingRuntimeState({
      cfg,
      logger: api.logger,
    });

    const {
      rememberSessionAgentId,
      resolveAgentId,
      resolvePluginSessionRouting,
      toQueryConfigContext,
    } = createOpenVikingSessionRoutingRuntime({
      peerPrefix: cfg.peer_prefix,
      logFindRequests: cfg.logFindRequests,
      logger: api.logger,
    });

    const recallTraceRuntime = createOpenVikingRecallTraceRuntime({
      getClient,
      resolvePluginSessionRouting,
      traceRecorder,
      registerRecallTraceRoutes: registerRecallTraceRouteAdapters,
      normalizeResourceTypes,
      clampScore,
      previewText,
      cfg,
    });
    const { queryRecallTraces, formatRecallTraceText, registerRecallTraceRoutes } = recallTraceRuntime;

    const handleQueryConfigCommand = createOpenVikingQueryConfigCommandHandler({
      resolvePluginSessionRouting,
      toQueryConfigContext,
      queryConfigStore,
      normalizeRuntimeQueryParams,
    });

    const { addResourceOpenViking, addSkillOpenViking } = createOpenVikingImportRuntime({
      getClient,
    });

    const queryRuntime = createOpenVikingQueryRuntime({
      getClient,
      queryConfigStore,
      toQueryConfigContext,
      traceRecorder,
      inferRecallResourceType,
      createTraceId,
      boundTraceQuery,
      previewText,
      logger: api.logger,
      cfg,
    });
    const {
      searchOpenViking,
      readOpenVikingContent,
      multiReadOpenVikingContent,
      listOpenVikingDirectory,
    } = queryRuntime;

    registerOpenVikingImportTools({
      registerTool: registerOpenVikingTool,
      getClient,
      resolvePluginSessionRouting,
      isBypassedSession,
      makeBypassedToolResult,
      enableAddResourceTool: cfg.enableAddResourceTool,
    });

    registerOpenVikingQueryTools({
      registerTool: registerOpenVikingTool,
      searchOpenViking,
      readOpenVikingContent,
      multiReadOpenVikingContent,
      listOpenVikingDirectory,
      resolvePluginSessionRouting,
      isBypassedSession,
      makeBypassedToolResult,
    });

    registerOpenVikingMemoryRecallTools({
      registerTool: registerOpenVikingTool,
      getClient,
      queryConfigStore,
      toQueryConfigContext,
      resolvePluginSessionRouting,
      isBypassedSession,
      makeBypassedToolResult,
      resolveRecallSearchPlan,
      postProcessMemories,
      pickMemoriesForInjection,
      buildMemoryLinesWithBudget,
      inferRecallResourceType,
      createTraceId,
      boundTraceQuery,
      previewText,
      traceRecorder,
      cfg,
      logger: api.logger,
    });

    registerOpenVikingRecallTraceTools({
      registerTool: registerOpenVikingTool,
      queryRecallTraces,
      formatRecallTraceText,
      resolvePluginSessionRouting,
      isBypassedSession,
      makeBypassedToolResult,
    });

    registerOpenVikingCommands(api, createOpenVikingCommandDefinitions({
      resolvePluginSessionRouting,
      isBypassedSession,
      makeBypassedToolResult,
      parseAddResourceCommandArgs,
      parseAddSkillCommandArgs,
      parseOVSearchCommandArgs,
      addResourceOpenViking,
      addSkillOpenViking,
      searchOpenViking,
      handleQueryConfigCommand,
      queryRecallTraces,
      formatRecallTraceText,
    }));

    registerOpenVikingMemoryTools({
      registerTool: registerOpenVikingTool,
      getClient,
      normalizeSessionId: openClawSessionRefToOvStorageId,
      createTempSessionId: createMemoryStoreTempSessionId,
      extractSenderId: extractToolSenderId,
      toRoleId,
      resolvePluginSessionRouting,
      isBypassedSession,
      makeBypassedToolResult,
      defaultTargetUri: cfg.targetUri,
      defaultRecallScoreThreshold: cfg.recallScoreThreshold,
      logFindRequests: cfg.logFindRequests,
      logger: api.logger,
    });

    registerOpenVikingArchiveTools({
      registerTool: registerOpenVikingTool,
      getClient,
      rememberSessionAgentId,
      toOvSessionId: openClawSessionToOvStorageId,
      resolveAgentId,
      resolvePluginSessionRouting,
      isBypassedSession,
      makeBypassedToolResult,
      formatMessage: formatMessageFaithful,
      traceRecorder,
      traceRecallMaxResultsPerSearch: cfg.traceRecallMaxResultsPerSearch,
      traceRecallPreviewChars: cfg.traceRecallPreviewChars,
      createTraceId,
      logger: api.logger,
    });

    registerOpenVikingToolResultTools({
      registerTool: registerOpenVikingTool,
      getClient,
      resolvePluginSessionRouting,
      isBypassedSession,
      makeBypassedToolResult,
      logger: api.logger,
    });

    const { getContextEngine, setContextEngineRef } = createOpenVikingContextEngineRef();

    registerOpenVikingLifecycleHooks({
      api,
      rememberSessionAgentId,
      isBypassedSession,
      verboseRoutingInfo,
      getContextEngine,
      logger: api.logger,
    });

    registerOpenVikingContextEngine({
      api,
      plugin: contextEnginePlugin,
      version: "0.1.0",
      cfg,
      logger: api.logger,
      getClient,
      resolveAgentId,
      rememberSessionAgentId,
      queryConfigStore,
      traceRecorder,
      createContextEngine: createMemoryOpenVikingContextEngine,
      setContextEngineRef,
    });

    registerSetupCli(api);
    const recallTraceHttpRoutesRegistered = registerRecallTraceRoutes(api);

    api.registerService(createOpenVikingService({
      cfg,
      getClient,
      logger: api.logger,
      recallTraceHttpRoutesRegistered,
      registerRecallTraceRoutes,
    }));
  },
};

export default contextEnginePlugin;
