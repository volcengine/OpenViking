export function registerOpenVikingContextEngine(deps) {
    if (typeof deps.api.registerContextEngine !== "function") {
        deps.logger.warn("openviking: registerContextEngine is unavailable; context-engine behavior will not run");
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
    deps.logger.info("openviking: registered context-engine (assemble=archive+active+auto-recall, afterTurn=auto-capture, session→OV id=uuid-or-sha256 + diag/Phase2 options)");
}
