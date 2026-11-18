import { createHash } from "node:crypto"
import { join } from "node:path"

// OpenCode v2 generate.text is stateless (no session, tools or hooks).
// Its API has no maxTokens argument. This bounds accepted text, not provider
// generation tokens; enabling it requires an explicitly selected model.
export function createV2Compressor(ctx, config, { signal, dataDir = config.dataDir } = {}) {
  const mode = config.recallRewrite ?? config.recallCompress ?? "off"
  const name = String(config.recallCompressModel || "").trim()
  const slash = name.indexOf("/")
  if (!["client", "auto"].includes(mode) || /\s/.test(name) || slash < 1 || slash === name.length - 1 ||
      !ctx?.experimental?.generate?.text || config.recallCompressBaseUrl) return null
  const model = { providerID: name.slice(0, slash), id: name.slice(slash + 1) }
  const active = new Map()
  const timeoutMs = Math.min(30000, Math.max(1, Number(config.recallCompressTimeoutMs) || 5000))
  const maxPromptChars = Math.min(204000, Math.max(1000, Number(config.recallCompressMaxInputChars) || 18000) + 4000)
  // Partition by the active connection as well as session and selected model.
  // Hash inputs together. Connection details never become a filename or cache entry.
  const scope = [config.endpoint, config.account, config.user, config.apiKey,
    config.effectivePeer?.peerId ?? config.peerId, model]
  return {
    cachePath(sessionID) {
      return dataDir ? join(dataDir, `recall-digest-${createHash("sha256").update(JSON.stringify([...scope, sessionID])).digest("hex")}.json`) : undefined
    },
    forSession(sessionID) {
      // Reserve before retrieval, so interruption also fences calls which have
      // not reached generation. Release the reservation when retrieval exits.
      const controller = new AbortController()
      const controllers = active.get(sessionID) ?? new Set()
      active.set(sessionID, controllers)
      controllers.add(controller)
      const abort = () => controller.abort()
      if (signal?.aborted) abort()
      else signal?.addEventListener("abort", abort, { once: true })
      const run = async (prompt) => {
        if (controller.signal.aborted || typeof prompt !== "string" || prompt.length > maxPromptChars) return null
        const generation = new AbortController()
        const abortGeneration = () => generation.abort()
        controller.signal.addEventListener("abort", abortGeneration, { once: true })
        const timer = setTimeout(abortGeneration, timeoutMs)
        let onAbort
        try {
          const cancelled = new Promise((resolve) => {
            onAbort = () => resolve(null)
            generation.signal.addEventListener("abort", onAbort, { once: true })
          })
          const result = await Promise.race([
            Promise.resolve().then(() => generation.signal.aborted ? null :
              ctx.experimental.generate.text({ prompt, model }, { signal: generation.signal })),
            cancelled,
          ])
          if (generation.signal.aborted || typeof result?.text !== "string" || result.text.length > 16000) return null
          return result.text
        } catch {
          return null
        } finally {
          clearTimeout(timer)
          generation.signal.removeEventListener("abort", onAbort)
          controller.signal.removeEventListener("abort", abortGeneration)
        }
      }
      return {
        run,
        signal: controller.signal,
        close() {
          signal?.removeEventListener("abort", abort)
          controllers.delete(controller)
          if (!controllers.size) active.delete(sessionID)
        },
      }
    },
    cancel(sessionID) {
      for (const controller of active.get(sessionID) ?? []) controller.abort()
    },
  }
}
