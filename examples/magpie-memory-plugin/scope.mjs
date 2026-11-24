import { isBypassed } from "./shared/session-model.mjs";
import { homedir } from "node:os";
import { join } from "node:path";
import { connectionHeaders } from "./http.mjs";
import { canonicalRecallValue, recallDigest } from "./recall-ledger.mjs";

export const bypassedSession = (cfg, ctx) => isBypassed(cfg, { sessionId: ctx.session, cwd: ctx.cwd });

export const dataDirectory = (cfg) => cfg.dataDir || join(homedir(), ".openviking", "magpie");
export function connectionScope(cfg, ctx, model) {
  const headers = Object.fromEntries(Object.entries(connectionHeaders(cfg))
    .filter(([name]) => name.toLowerCase() !== "user-agent"));
  return recallDigest(JSON.stringify(canonicalRecallValue({
    url: cfg.baseUrl, headers, configPath: cfg.path,
    agent: ctx.agent || "", session: ctx.session, cwd: ctx.cwd || "", protocol: ctx.protocol,
    ...(model === undefined ? {} : { model, peerScope: cfg.recallPeerScope }),
  })));
}
