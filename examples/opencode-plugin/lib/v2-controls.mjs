import { diagnoseOpenCode, sanitizeDiagnosticText } from "./diagnostics.mjs"
import { isCaptureEnabled } from "./shared/capture-utils.mjs"

import { inspectV2Recall } from "./v2-recall-inspector.mjs"
import { OPENVIKING_CONTROLS } from "./controls-contract.mjs"
export { OPENVIKING_CONTROLS } from "./controls-contract.mjs"

async function getOwnedSession(ctx, sessionID) {
  const session = await ctx.session.get({ sessionID })
  if (session?.location?.directory !== ctx.location.directory ||
      (ctx.location.project?.id && session.projectID !== ctx.location.project.id)) {
    throw new Error("Session belongs to another OpenCode location")
  }
  return session
}

export async function registerV2Controls(ctx, runtime, { pluginRoot, serializeSession, captureCurrent }) {
  if (!ctx?.rpc?.register) return
  const diagnose = async (input = {}, { signal } = {}) => diagnoseOpenCode({
    ctx, config: runtime.config, pluginRoot, offline: input.offline === true, signal,
  })
  await ctx.rpc.register(OPENVIKING_CONTROLS, {
    status: diagnose,
    doctor: diagnose,
    recall: async ({ sessionID }, { error: rpcError } = {}) => {
      try {
        await getOwnedSession(ctx, sessionID)
        return await inspectV2Recall(ctx, runtime.config, sessionID)
      } catch (error) {
        if (!rpcError) throw error
        return rpcError("operation_failed", "Could not inspect this session's stored OpenViking prompt context", {})
      }
    },
    commit: async ({ sessionID }, { signal, error: rpcError } = {}) => {
      try {
        signal?.throwIfAborted()
        if (!isCaptureEnabled(runtime.config)) return { status: "disabled", extraction: "not_started" }
        // Caller selects its current session; never flushAll or accept an OV id.
        const session = await getOwnedSession(ctx, sessionID)
        if (runtime.config.skipSubagentSessions && session.parentID) return { status: "disabled", extraction: "not_started" }
        return await serializeSession(async () => {
          await runtime.ready
          signal?.throwIfAborted()
          // Capture reads the host context and uses the existing privacy filters.
          await captureCurrent(sessionID, signal)
          const result = await runtime.sessionManager.commitCurrentSession(sessionID, signal)
          return { status: result.status, traceId: result.traceId ?? null,
            extraction: result.status === "accepted" ? "not_verified" : "not_started" }
        })
      } catch (error) {
        if (!rpcError) throw error
        const message = sanitizeDiagnosticText(runtime.config, error.message ?? error)
        return rpcError("operation_failed", message, {})
      }
    },
  })
}
