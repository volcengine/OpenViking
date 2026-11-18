import { diagnoseOpenCode, sanitizeDiagnosticText } from "./diagnostics.mjs"
import { isCaptureEnabled } from "./shared/capture-utils.mjs"

const sessionInput = { type: "object", properties: { sessionID: { type: "string", minLength: 1 } }, required: ["sessionID"], additionalProperties: false }
const readInput = { type: "object", properties: { offline: { type: "boolean" } }, additionalProperties: false }
// JSON Schema is the portable definition accepted by both plugin hosts.
export const OPENVIKING_CONTROLS = {
  id: "openviking.controls",
  methods: {
    status: { input: readInput, output: { type: "object" } },
    doctor: { input: readInput, output: { type: "object" } },
    commit: { input: sessionInput, output: { type: "object" }, errors: { operation_failed: { type: "object" } } },
  },
  events: {},
}

export async function registerV2Controls(ctx, runtime, { pluginRoot, serializeSession, captureCurrent }) {
  if (!ctx?.rpc?.register) return
  const diagnose = async (input = {}, { signal } = {}) => diagnoseOpenCode({
    ctx, config: runtime.config, pluginRoot, offline: input.offline === true, signal,
  })
  await ctx.rpc.register(OPENVIKING_CONTROLS, {
    status: diagnose,
    doctor: diagnose,
    commit: async ({ sessionID }, { signal, error: rpcError } = {}) => {
      try {
        signal?.throwIfAborted()
        if (!isCaptureEnabled(runtime.config)) return { status: "disabled", extraction: "not_started" }
        // Caller selects its current session; never flushAll or accept an OV id.
        const session = await ctx.session.get({ sessionID })
        if (session.location.directory !== ctx.location.directory ||
          (ctx.location.project?.id && session.projectID !== ctx.location.project.id)) throw new Error("Session belongs to another OpenCode location")
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
