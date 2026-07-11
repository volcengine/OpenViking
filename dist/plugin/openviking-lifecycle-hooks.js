export function registerOpenVikingLifecycleHooks(deps) {
    deps.api.on("session_start", async (_event, ctx) => {
        deps.rememberSessionAgentId(ctx ?? {});
    });
    deps.api.on("session_end", async (_event, ctx) => {
        deps.rememberSessionAgentId(ctx ?? {});
    });
    deps.api.on("before_reset", async (_event, ctx) => {
        if (deps.isBypassedSession(ctx)) {
            deps.verboseRoutingInfo(`openviking: bypassing before_reset due to session pattern match (sessionKey=${ctx?.sessionKey ?? "none"}, sessionId=${ctx?.sessionId ?? "none"})`);
            return;
        }
        const sessionId = ctx?.sessionId;
        const contextEngine = deps.getContextEngine();
        if (sessionId && contextEngine) {
            try {
                const ok = await contextEngine.commitOVSession({
                    sessionId,
                    sessionKey: ctx?.sessionKey,
                });
                if (ok) {
                    deps.logger.info(`openviking: committed OV session on reset for session=${sessionId}`);
                }
            }
            catch (err) {
                deps.logger.warn(`openviking: failed to commit OV session on reset: ${String(err)}`);
            }
        }
    });
    deps.api.on("after_compaction", async (_event, _ctx) => {
        // Reserved hook registration for future post-compaction memory integration.
    });
}
