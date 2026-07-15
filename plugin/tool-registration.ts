export type OpenVikingToolRegistrationApi = {
  registerTool: (toolOrFactory: unknown, opts: { name: string }) => void;
};

export type OpenVikingToolRegistrationLogger = {
  debug?: (message: string) => void;
};

export type OpenVikingToolRegistrarOptions = {
  api: OpenVikingToolRegistrationApi;
  enabledToolNames: ReadonlySet<string>;
  logger?: OpenVikingToolRegistrationLogger;
};

export type OpenVikingToolRegistrationRuntimeConfig = {
  enabledTools: string[] | string;
};

export function createOpenVikingToolRegistrar({
  api,
  enabledToolNames,
  logger,
}: OpenVikingToolRegistrarOptions) {
  return (toolOrFactory: unknown, opts: { name: string }): void => {
    if (!enabledToolNames.has(opts.name)) {
      logger?.debug?.(`openviking: tool ${opts.name} disabled by config`);
      return;
    }
    api.registerTool(toolOrFactory, opts);
  };
}

export function createOpenVikingToolRegistrationRuntime<TConfig extends OpenVikingToolRegistrationRuntimeConfig>(options: {
  api: OpenVikingToolRegistrationApi;
  cfg: TConfig;
  logger?: OpenVikingToolRegistrationLogger;
}) {
  const enabledTools = Array.isArray(options.cfg.enabledTools)
    ? options.cfg.enabledTools
    : [options.cfg.enabledTools];
  const enabledToolNames = new Set<string>(enabledTools);
  const registerOpenVikingTool = createOpenVikingToolRegistrar({
    api: options.api,
    enabledToolNames,
    logger: options.logger,
  });

  return {
    registerOpenVikingTool,
  };
}
