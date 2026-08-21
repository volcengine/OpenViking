export function createOpenVikingService({ cfg, getClient, logger, recallTraceHttpRoutesRegistered, registerRecallTraceRoutes, }) {
    return {
        id: "openviking",
        start: async (ctx) => {
            const runtimeRouteRegistered = registerRecallTraceRoutes(ctx);
            const routeRegistered = recallTraceHttpRoutesRegistered || runtimeRouteRegistered;
            await (await getClient()).healthCheck().catch(() => { });
            logger.info(`openviking: initialized (url: ${cfg.baseUrl}, targetUri: ${cfg.targetUri}, search: hybrid endpoint)`);
            if (routeRegistered) {
                logger.info("openviking: registered recall trace Gateway routes");
            }
            else {
                logger.warn?.("openviking: recall trace Gateway route adapter unavailable; use ov_recall_trace tool or /ov-recall-trace command");
            }
        },
        stop: () => {
            logger.info("openviking: stopped");
        },
    };
}
