import { log } from "./utils.mjs"

const METADATA_KEY = "openviking"
const LEDGER_KEY_PREFIX = "recall-ledger/"
const LEDGER_VERSION = 1
// Bounded so a very long session cannot grow its ledger without limit.
const MAX_LEDGER_ENTRIES = 500
// A prompt that never reaches a model request (interrupted, rejected) leaves
// its prefetch behind; drop it once it is too old to be waited on.
const PENDING_TTL_MS = 10 * 60 * 1000

/**
 * Recall for OpenCode v2 that does not hold the send.
 *
 * The host awaits the `prompt` hook before it admits the user message, so a
 * network call there is time the user spends looking at a message that has
 * not appeared yet (#5148). Here `prompt` only starts the fetch. The `context`
 * hook, which runs right before each model request, waits for it for what is
 * left of `recallWaitMs` and injects whatever arrived.
 *
 * `context` edits are made per request and never persisted, unlike prompt
 * metadata. The block chosen for a message is therefore recorded in a ledger
 * and replayed byte for byte on every later request, so an old user message
 * reads the same after a restart and the provider's prefix cache keeps
 * hitting. The ledger lives in host storage, one key per session.
 */
export function createV2Recall({
  ctx,
  config,
  ready = Promise.resolve(),
  recall,
  sessionInject,
  directory,
  recallEnabled,
  now = Date.now,
}) {
  const waitMs = Math.max(0, Number(config?.recallWaitMs ?? 5000))
  const persist = config?.recallLedger !== false
  const pending = new Map()
  const ledgers = new Map()
  // Session-start context that missed its message's budget. Profile and
  // archive are not tied to one query, so they ride on the next message
  // instead of being dropped; the message they missed is never rewritten.
  const carryover = new Map()

  function prefetch(event) {
    const sessionID = event?.sessionID
    const messageID = event?.messageID
    if (!sessionID || !messageID) return
    prune()
    const text = event?.prompt?.text
    const parts = typeof text === "string" && text.trim() ? [{ type: "text", text }] : []
    const input = { sessionID, messageID, directory }
    const carried = carryover.get(sessionID) ?? []
    carryover.delete(sessionID)
    pending.set(messageID, {
      sessionID,
      startedAt: now(),
      carried,
      session: settle(() => sessionInject.buildSessionContext(input), "session.prompt.profile"),
      memories: recallEnabled
        ? settle(() => recall.buildRelevantMemories(input, parts), "session.prompt.recall")
        : Promise.resolve(undefined),
    })
  }

  async function inject(event) {
    const sessionID = event?.sessionID
    if (!sessionID || !Array.isArray(event?.messages)) return
    const ledger = await loadLedger(sessionID)
    let recorded = false
    for (const message of event.messages) {
      if (message?.role !== "user" || !message.id) continue
      if (Array.isArray(message.content) && message.content.some((part) => part?.metadata?.[METADATA_KEY] === true)) continue
      let text = ledger.get(message.id)
      if (text === undefined) {
        const entry = pending.get(message.id)
        if (entry) {
          pending.delete(message.id)
          text = await resolve(entry, message.id)
          if (text) {
            record(ledger, message.id, text)
            recorded = true
          }
        } else {
          // Messages admitted by releases that awaited recall in `prompt`
          // carry their blocks in persisted metadata.
          text = joinBlocks(message.metadata?.[METADATA_KEY]?.context)
        }
      }
      if (text) prepend(message, text)
    }
    if (recorded) await saveLedger(sessionID, ledger)
  }

  async function forget(sessionID) {
    for (const [messageID, entry] of pending) {
      if (entry.sessionID === sessionID) pending.delete(messageID)
    }
    carryover.delete(sessionID)
    ledgers.delete(sessionID)
    if (!persist || !ctx?.storage?.remove) return
    try {
      await ctx.storage.remove(LEDGER_KEY_PREFIX + sessionID)
    } catch (error) {
      logHookError("storage.remove", error)
    }
  }

  async function resolve(entry, messageID) {
    const remaining = Math.max(0, entry.startedAt + waitMs - now())
    let timer
    const deadline = new Promise((done) => {
      timer = setTimeout(done, remaining)
      timer.unref?.()
    })
    const inTime = (promise) => Promise.race([promise.then((value) => ({ value })), deadline.then(() => null)])
    const [session, memories] = await Promise.all([inTime(entry.session), inTime(entry.memories)])
    clearTimeout(timer)

    if (!session) {
      entry.session.then((block) => {
        if (!block) return
        carryover.set(entry.sessionID, [...(carryover.get(entry.sessionID) ?? []), block])
      })
    }
    if (!session || !memories) {
      log("INFO", "recall", "OpenViking context missed the wait budget", {
        opencode_session: entry.sessionID,
        message: messageID,
        waitMs,
        session: session ? "ready" : "late",
        recall: memories ? "ready" : "late",
      })
    }
    const text = joinBlocks([...entry.carried, session?.value, memories?.value])
    if (text) {
      log("INFO", "recall", "Injected OpenViking context", {
        opencode_session: entry.sessionID,
        waitedMs: Math.max(0, now() - entry.startedAt),
      })
    }
    return text
  }

  function settle(start, hook) {
    return Promise.resolve(ready)
      .then(start)
      .catch((error) => {
        logHookError(hook, error)
        return undefined
      })
  }

  function prune() {
    const cutoff = now() - PENDING_TTL_MS
    for (const [messageID, entry] of pending) {
      if (entry.startedAt < cutoff) pending.delete(messageID)
    }
  }

  function loadLedger(sessionID) {
    let ledger = ledgers.get(sessionID)
    if (!ledger) {
      ledger = readLedger(sessionID)
      ledgers.set(sessionID, ledger)
    }
    return ledger
  }

  async function readLedger(sessionID) {
    const ledger = new Map()
    if (!persist || !ctx?.storage?.get) return ledger
    try {
      const value = await ctx.storage.get(LEDGER_KEY_PREFIX + sessionID)
      if (value?.version === LEDGER_VERSION && Array.isArray(value.entries)) {
        for (const entry of value.entries) {
          if (Array.isArray(entry) && typeof entry[0] === "string" && typeof entry[1] === "string") {
            ledger.set(entry[0], entry[1])
          }
        }
      }
    } catch (error) {
      // An unreadable ledger costs one cache miss per old message, nothing more.
      logHookError("storage.get", error)
    }
    return ledger
  }

  async function saveLedger(sessionID, ledger) {
    if (!persist || !ctx?.storage?.set) return
    try {
      await ctx.storage.set(LEDGER_KEY_PREFIX + sessionID, {
        version: LEDGER_VERSION,
        entries: [...ledger],
      })
    } catch (error) {
      logHookError("storage.set", error)
    }
  }

  return { prefetch, inject, forget }
}

function record(ledger, messageID, text) {
  ledger.set(messageID, text)
  while (ledger.size > MAX_LEDGER_ENTRIES) {
    ledger.delete(ledger.keys().next().value)
  }
}

function joinBlocks(blocks) {
  if (!Array.isArray(blocks)) return ""
  return blocks.filter((block) => typeof block === "string" && block).join("\n\n")
}

function prepend(message, text) {
  if (typeof message.content === "string") message.content = [{ type: "text", text: message.content }]
  if (!Array.isArray(message.content)) message.content = []
  message.content.unshift({
    type: "text",
    text,
    metadata: { [METADATA_KEY]: true },
  })
}

function logHookError(hook, error) {
  log("WARN", "v2", `OpenCode v2 ${hook} failed`, {
    error: error?.message ?? String(error),
  })
}
