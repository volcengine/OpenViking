export function createOpenVikingToolRegistrar({ api, enabledToolNames, logger, }) {
    return (toolOrFactory, opts) => {
        if (!enabledToolNames.has(opts.name)) {
            logger?.debug?.(`openviking: tool ${opts.name} disabled by config`);
            return;
        }
        api.registerTool(toolOrFactory, opts);
    };
}
export function createOpenVikingToolRegistrationRuntime(options) {
    const enabledTools = Array.isArray(options.cfg.enabledTools)
        ? options.cfg.enabledTools
        : [options.cfg.enabledTools];
    const enabledToolNames = new Set(enabledTools);
    const registerOpenVikingTool = createOpenVikingToolRegistrar({
        api: options.api,
        enabledToolNames,
        logger: options.logger,
    });
    return {
        registerOpenVikingTool,
    };
}
