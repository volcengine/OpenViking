export type OpenVikingContextEngineRegistrationApi = {
  registerContextEngine?: (id: string, factory: () => unknown) => void;
};

export type OpenVikingContextEngineLogger = {
  info: (message: string) => void;
  warn: (message: string) => void;
};

export type OpenVikingContextEnginePluginInfo = {
  id: string;
  name: string;
};

export type OpenVikingContextEngineCreateParams<
  TCfg = unknown,
  TClient = unknown,
  TQueryConfigStore = unknown,
  TTraceRecorder = unknown,
  TLogger extends OpenVikingContextEngineLogger = OpenVikingContextEngineLogger,
> = {
  id: string;
  name: string;
  version: string;
  cfg: TCfg;
  logger: TLogger;
  getClient: () => Promise<TClient>;
  resolveAgentId: (sessionId: string, sessionKey?: string, ovSessionId?: string) => string;
  rememberSessionAgentId: (ctx: {
    agentId?: string;
    sessionId?: string;
    sessionKey?: string;
    ovSessionId?: string;
  }) => void;
  queryConfigStore: TQueryConfigStore;
  traceRecorder: TTraceRecorder;
};

export type OpenVikingContextEngineRegistrationDeps<
  TEngine,
  TCfg = unknown,
  TClient = unknown,
  TQueryConfigStore = unknown,
  TTraceRecorder = unknown,
  TLogger extends OpenVikingContextEngineLogger = OpenVikingContextEngineLogger,
> = {
  api: OpenVikingContextEngineRegistrationApi;
  plugin: OpenVikingContextEnginePluginInfo;
  version: string;
  cfg: TCfg;
  logger: TLogger;
  getClient: () => Promise<TClient>;
  resolveAgentId: (sessionId: string, sessionKey?: string, ovSessionId?: string) => string;
  rememberSessionAgentId: OpenVikingContextEngineCreateParams["rememberSessionAgentId"];
  queryConfigStore: TQueryConfigStore;
  traceRecorder: TTraceRecorder;
  createContextEngine: (params: OpenVikingContextEngineCreateParams<TCfg, TClient, TQueryConfigStore, TTraceRecorder, TLogger>) => TEngine;
  setContextEngineRef: (engine: TEngine) => void;
};

export function registerOpenVikingContextEngine<
  TEngine,
  TCfg,
  TClient,
  TQueryConfigStore,
  TTraceRecorder,
  TLogger extends OpenVikingContextEngineLogger,
>(
  deps: OpenVikingContextEngineRegistrationDeps<TEngine, TCfg, TClient, TQueryConfigStore, TTraceRecorder, TLogger>,
): void {
  if (typeof deps.api.registerContextEngine !== "function") {
    deps.logger.warn(
      "openviking: registerContextEngine is unavailable; context-engine behavior will not run",
    );
    return;
  }

  deps.api.registerContextEngine(deps.plugin.id, () => {
    const contextEngine = deps.createContextEngine({
      id: deps.plugin.id,
      name: deps.plugin.name,
      version: deps.version,
      cfg: deps.cfg,
      logger: deps.logger,
      getClient: deps.getClient,
      resolveAgentId: deps.resolveAgentId,
      rememberSessionAgentId: deps.rememberSessionAgentId,
      queryConfigStore: deps.queryConfigStore,
      traceRecorder: deps.traceRecorder,
    });
    deps.setContextEngineRef(contextEngine);
    return contextEngine;
  });
  deps.logger.info(
    "openviking: registered context-engine (assemble=archive+active+auto-recall, afterTurn=auto-capture, session→OV id=uuid-or-sha256 + diag/Phase2 options)",
  );
}
