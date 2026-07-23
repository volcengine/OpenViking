import { buildRecallBlock } from "./shared/recall-core.mjs"
import { isBypassed } from "./shared/session-model.mjs"
import { effectivePeerId, fetchJSON, log } from "./utils.mjs"

export function createMemoryRecall({ config }) {
  async function injectRelevantMemories(input, output) {
    if (!config.autoRecall?.enabled) return
    const query = extractCurrentUserText(output.parts ?? [])
    if (!query) return
    if (query.length < config.minQueryLength) return
    if (isBypassed(config, {
      sessionId: input.sessionID ?? output.message?.sessionID,
      cwd: input.directory ?? input.cwd,
    })) return

    const health = await fetchJSON(config, "/health", {}, { timeoutMs: 5000 })
    if (!health.ok) return

    const block = await buildRecallBlock(
      (path, init = {}, options = {}) => fetchJSON(config, path, init, { ...options, timeoutMs: 5000 }),
      config,
      query,
      {
        actorPeerId: effectivePeerId(config),
        log: (stage, data) => log("DEBUG", "recall", stage, data),
      },
    )
    if (!block) return

    if (prependSyntheticRecallPart(input, output, block)) {
      log("INFO", "recall", "Injected OpenViking context")
    }
  }

  return { injectRelevantMemories }
}

export function extractCurrentUserText(parts) {
  const texts = []
  for (const part of parts) {
    if (part.type !== "text" || typeof part.text !== "string") continue
    if (part.synthetic || part.ignored) continue
    if (part.text.includes("<openviking-context")) return null
    texts.push(part.text)
  }
  const joined = texts.join(" ").trim()
  return joined || null
}

function prependSyntheticRecallPart(input, output, injection) {
  const sessionID = input.sessionID ?? output.message?.sessionID
  const messageID = input.messageID ?? output.message?.id
  if (!sessionID || !messageID) return false

  output.parts.unshift({
    id: `prt-ov-recall-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type: "text",
    text: injection,
    synthetic: true,
    sessionID,
    messageID,
  })
  return true
}
