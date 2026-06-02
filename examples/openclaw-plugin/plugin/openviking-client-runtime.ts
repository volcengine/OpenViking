import type { HttpTransport } from "../adapters/http-transport.js";
import { OpenVikingClient } from "../client.js";

type Logger = {
  info: (message: string) => void;
};

type ClientRuntimeConfig = {
  baseUrl: string;
  apiKey: string;
  agent_prefix: string;
  timeoutMs: number;
  accountId?: string;
  userId?: string;
  logFindRequests: boolean;
  isolateUserScopeByAgent: boolean;
  isolateAgentScopeByUser: boolean;
  agentScopeMode?: string;
};

export function createOpenVikingClientRuntime(options: {
  cfg: ClientRuntimeConfig;
  rawAgentId: unknown;
  logger: Logger;
  transport?: HttpTransport;
}) {
  const { cfg, logger } = options;

  if (cfg.logFindRequests) {
    logger.info(
      "openviking: routing debug logging enabled (config logFindRequests, or env OPENVIKING_LOG_ROUTING=1 / OPENVIKING_DEBUG=1)",
    );
  }

  const verboseRoutingInfo = (message: string) => {
    if (cfg.logFindRequests) {
      logger.info(message);
    }
  };

  verboseRoutingInfo(
    `openviking: loaded plugin config agent_prefix="${cfg.agent_prefix}" ` +
      `(raw plugins.entries.openviking.config.agent_prefix=${JSON.stringify(options.rawAgentId ?? "(missing)")}; ` +
      `${
        cfg.agent_prefix
          ? "non-empty → X-OpenViking-Agent is <agent_prefix>_<ctx.agentId> when hooks expose session agent, or <agent_prefix>_main when ctx.agentId is unknown"
          : "empty → X-OpenViking-Agent follows OpenClaw ctx.agentId per session, or \"main\" when ctx.agentId is unknown"
      })`,
  );
  verboseRoutingInfo(
    `openviking: auth/namespace config ` +
      JSON.stringify({
        isolateUserScopeByAgent: cfg.isolateUserScopeByAgent,
        isolateAgentScopeByUser: cfg.isolateAgentScopeByUser,
        deprecatedAgentScopeMode: cfg.agentScopeMode,
      }),
  );

  const routingDebugLog = cfg.logFindRequests
    ? (msg: string) => {
        logger.info(msg);
      }
    : undefined;

  const clientPromise = Promise.resolve(
    new OpenVikingClient(
      cfg.baseUrl,
      cfg.apiKey,
      cfg.agent_prefix,
      cfg.timeoutMs,
      cfg.accountId,
      cfg.userId,
      routingDebugLog,
      cfg.isolateUserScopeByAgent,
      cfg.isolateAgentScopeByUser,
      options.transport ? { transport: options.transport } : undefined,
    ),
  );

  const getClient = (): Promise<OpenVikingClient> => clientPromise;

  return {
    getClient,
    verboseRoutingInfo,
  };
}
