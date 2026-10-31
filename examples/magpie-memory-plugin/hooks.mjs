import { recallRequest } from "./runtime.mjs";
import { loadConfig } from "./config.mjs";
import { prepareCapture, finishCapture } from "./capture.mjs";
import { isAuxiliaryRequest } from "./conversation.mjs";

export async function onRequest(body, ctx) {
  if (isAuxiliaryRequest(body, ctx)) { ctx.report?.("skipped", 0); return; }
  try {
    const cfg = await loadConfig(process.env, ctx.options || {});
    ctx.state ||= {};
    try { await prepareCapture(body, ctx, cfg, ctx.options); }
    catch { delete ctx.state.capture; }
    if (ctx.state.nativeOwner) { ctx.report?.("skipped", 0); return; }
    return await recallRequest(body, ctx, process.env, cfg);
  } catch { ctx.report?.("unavailable", 0); }
}

export async function onResponse(reply, ctx) {
  if (!ctx.state?.capture) return;
  try {
    const cfg = await loadConfig(process.env, ctx.state?.capture?.options || ctx.options || {});
    await finishCapture(reply, ctx, cfg);
  } catch { /* Capture failures retain pending snapshots and never fail model output. */ }
}
