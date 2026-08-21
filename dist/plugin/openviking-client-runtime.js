import { OpenVikingClient } from "../client.js";
import { resolveOpenVikingRequestHeaders } from "../request-headers.js";
export function createOpenVikingClientRuntime(options) {
    const { cfg, logger } = options;
    if (cfg.logFindRequests) {
        logger.info("openviking: routing debug logging enabled (config logFindRequests, or env OPENVIKING_LOG_ROUTING=1 / OPENVIKING_DEBUG=1)");
    }
    const verboseRoutingInfo = (message) => {
        if (cfg.logFindRequests) {
            logger.info(message);
        }
    };
    verboseRoutingInfo(`openviking: loaded plugin config peer_role="${cfg.peer_role}" peer_prefix="${cfg.peer_prefix}" ` +
        `(raw peer_prefix=${JSON.stringify(options.rawPeerPrefix ?? "(missing)")}; ` +
        `${cfg.peer_prefix
            ? 'non-empty → assistant peer_id is <peer_prefix>_<ctx.agentId> when peer_role="assistant", or <peer_prefix>_main when ctx.agentId is unknown'
            : 'empty → assistant peer_id follows OpenClaw ctx.agentId when peer_role="assistant", or "main" when ctx.agentId is unknown'})`);
    const routingDebugLog = cfg.logFindRequests
        ? (msg) => {
            logger.info(msg);
        }
        : undefined;
    const clientPromise = Promise.resolve(new OpenVikingClient(cfg.baseUrl, cfg.apiKey, cfg.peer_prefix, cfg.timeoutMs, cfg.accountId, cfg.userId, routingDebugLog, {
        transport: options.transport,
        headers: resolveOpenVikingRequestHeaders({
            headers: cfg.headers,
        }),
    }));
    const getClient = () => clientPromise;
    return {
        getClient,
        verboseRoutingInfo,
    };
}
