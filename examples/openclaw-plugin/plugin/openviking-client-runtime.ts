import { OpenVikingClient } from "../client.js";
import type { HttpTransport } from "../adapters/http-transport.js";

type Logger = {
  info: (message: string) => void;
};

type ClientRuntimeConfig = {
  baseUrl: string;
  apiKey: string;
  peer_role: "none" | "assistant" | "person";
  peer_prefix: string;
  timeoutMs: number;
  accountId?: string;
  userId?: string;
  logFindRequests: boolean;
};

export function createOpenVikingClientRuntime(options: {
  cfg: ClientRuntimeConfig;
  rawPeerPrefix: unknown;
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
    `openviking: loaded plugin config peer_role="${cfg.peer_role}" peer_prefix="${cfg.peer_prefix}" ` +
      `(raw peer_prefix=${JSON.stringify(options.rawPeerPrefix ?? "(missing)")}; ` +
      `${
        cfg.peer_prefix
          ? 'non-empty → assistant peer_id is <peer_prefix>_<ctx.agentId> when peer_role="assistant", or <peer_prefix>_main when ctx.agentId is unknown'
          : 'empty → assistant peer_id follows OpenClaw ctx.agentId when peer_role="assistant", or "main" when ctx.agentId is unknown'
      })`,
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
      cfg.peer_prefix,
      cfg.timeoutMs,
      cfg.accountId,
      cfg.userId,
      routingDebugLog,
      { transport: options.transport },
    ),
  );

  const getClient = (): Promise<OpenVikingClient> => clientPromise;

  return {
    getClient,
    verboseRoutingInfo,
  };
}
