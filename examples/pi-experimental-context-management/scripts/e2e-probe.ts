/**
 * Probe extension for live e2e runs.
 *
 * Loaded next to the OpenViking extension to capture the exact provider
 * payload pi sends after context hooks have run.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export default function (pi: ExtensionAPI) {
  const outDir = process.env.OV_E2E_OUT;
  if (!outDir) return;
  mkdirSync(outDir, { recursive: true });

  const turn = process.env.OV_E2E_TURN ?? "0";
  let n = 0;

  const writeSessionId = (ctx: { sessionManager?: { getSessionId?: () => string } }) => {
    try {
      const id = ctx.sessionManager?.getSessionId?.();
      if (id) writeFileSync(join(outDir, "session-id.txt"), id);
    } catch {
      // Best effort.
    }
  };

  pi.on("session_start", async (_event, ctx) => writeSessionId(ctx));
  pi.on("before_agent_start", async (_event, ctx) => writeSessionId(ctx));

  pi.on("before_provider_request", async (event, _ctx) => {
    n++;
    try {
      writeFileSync(
        join(outDir, `payload-t${turn}-${String(n).padStart(2, "0")}.json`),
        JSON.stringify(event.payload, null, 2),
      );
    } catch {
      // Best effort.
    }
  });
}
