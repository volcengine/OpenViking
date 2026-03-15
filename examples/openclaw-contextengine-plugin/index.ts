import { createOpenVikingClient } from "./client.js";
import { parseConfig } from "./config.js";
import { OpenVikingContextEngine } from "./context-engine.js";
import { createTools } from "./tools.js";

const plugin = {
  id: "contextengine-openviking",
  kind: "context-engine" as const,
  register(api: {
    pluginConfig?: unknown;
    registerContextEngine: (id: string, factory: () => unknown) => void;
    registerTool?: (tool: unknown) => void;
  }) {
    const config = parseConfig(api.pluginConfig ?? {});
    const client = createOpenVikingClient({
      baseUrl: config.connection.baseUrl,
      timeoutMs: config.connection.timeoutMs,
      apiKey: config.connection.apiKey,
      agentId: config.connection.agentId,
    });

    api.registerContextEngine("contextengine-openviking", () =>
      new OpenVikingContextEngine({
        config,
        client,
      }),
    );

    if (api.registerTool) {
      for (const tool of createTools({ client })) {
        api.registerTool(tool);
      }
    }
  },
};

export default plugin;
