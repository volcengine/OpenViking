import { isRecallEnabled } from "./shared/recall-core.mjs"
import { createOpenVikingV2McpConfig } from "./mcp-config.mjs"
import { contextMessageEvents, normalizeV2LifecycleEvent } from "./v2-events.mjs"
import { log } from "./utils.mjs"

const METADATA_KEY = "openviking"
const CURSOR_KEY_PREFIX = "capture-cursor/"
const LIFECYCLE_EVENTS = new Set([
  "session.created",
  "session.deleted",
  "session.execution.succeeded",
  "session.execution.failed",
  "session.execution.interrupted",
  "session.compaction.ended",
])

export async function startV2Plugin(ctx, runtime, { pluginRoot }) {
  const {
    config,
    sessionManager,
    repoContext,
    recall,
    sessionInject,
    vikingUriGuard,
    vikingUriNotice,
  } = runtime
  const captureCursors = new Map()
  const sessionOwnership = new Map()
  // Compaction hooks can run while lifecycle events are still flushing. Keep
  // capture and flush ordered so the same pending message is never sent twice.
  let sessionTask = Promise.resolve()
  const serializeSession = (operation) => {
    const task = sessionTask.then(operation)
    sessionTask = task.catch(() => {})
    return task
  }
  const directory = ctx?.location?.project?.directory || ctx?.location?.directory

  if (config.mcp.enabled && ctx?.mcp?.transform) {
    await ctx.mcp.transform((editor) => {
      const current = editor.get?.("openviking")
      if (current?.disabled === true) return
      editor.set("openviking", createOpenVikingV2McpConfig(pluginRoot))
    })
    log("INFO", "mcp", "Registered OpenViking MCP server for OpenCode v2")
  } else if (!config.mcp.enabled) {
    log("INFO", "mcp", "Skipped bundled MCP registration in hook-only mode")
  }

  if (ctx?.tool?.hook) {
    await ctx.tool.hook("execute.before", async (event) => {
      await runtime.ready
      await vikingUriGuard(
        { tool: event.tool, args: event.input },
        { args: event.input },
      )
    })
    await ctx.tool.hook("execute.after", async (event) => {
      try {
        await runtime.ready
        await noticeV2Tool(event, vikingUriNotice)
      } catch (error) {
        logHookError("tool.execute.after", error)
      }
    })
  }

  if (ctx?.session?.hook) {
    await ctx.session.hook("prompt", async (event) => {
      try {
        await runtime.ready
        await prepareV2Prompt(event, {
          directory,
          recall,
          sessionInject,
          recallEnabled: isRecallEnabled(config),
        })
      } catch (error) {
        logHookError("session.prompt", error)
      }
    })

    await ctx.session.hook("context", async (event) => {
      try {
        await runtime.ready
        injectV2Context(event, repoContext)
      } catch (error) {
        logHookError("session.context", error)
      }
    })

    await ctx.session.hook("compaction", async (event) => {
      try {
        await runtime.ready
        // context() drops the old transcript as soon as compaction completes.
        // Capture it now; the ended event below owns the commit.
        await serializeSession(() => captureV2Context(ctx, sessionManager, captureCursors, event.sessionID))
      } catch (error) {
        logHookError("session.compaction.capture", error)
      }
    })
  }

  const controller = new AbortController()
  let eventTask = Promise.resolve()
  if (ctx?.event?.subscribe) {
    eventTask = consumeEvents(ctx, controller.signal, async (event) => {
      const sessionID = event?.data?.sessionID ?? event?.data?.sessionId
      if (!LIFECYCLE_EVENTS.has(event?.type) || !sessionID) return
      if (!(await ownsSession(ctx, sessionOwnership, event, sessionID))) return
      await serializeSession(async () => {
        await runtime.ready
        if (event.type === "session.execution.failed") {
          log("WARN", "v2", "OpenCode v2 execution failed", {
            opencode_session: sessionID,
            error: event.data?.error?.message ?? event.data?.error,
          })
        }
        if (isExecutionBoundary(event.type)) {
          try {
            await captureV2Context(ctx, sessionManager, captureCursors, sessionID)
          } catch (error) {
            logHookError("session.context.capture", error)
          }
        }
        for (const normalized of normalizeV2LifecycleEvent(event)) {
          await sessionManager.handleEvent(normalized)
          if (normalized.type === "session.created") {
            await repoContext.refreshRepos({ force: true })
          }
        }
        if (event.type === "session.deleted") {
          sessionOwnership.delete(sessionID)
          await forgetCursor(ctx, captureCursors, sessionID)
        }
      })
    })
  }

  return async () => {
    controller.abort()
    try {
      await eventTask
      await sessionTask
      await runtime.ready
      await runtime.background
      await sessionManager.waitForBackground?.()
      await sessionManager.flushAll({ commit: true })
    } catch (error) {
      logHookError("plugin.cleanup", error)
    }
    log("INFO", "plugin", "OpenViking plugin disposed")
  }
}

async function consumeEvents(ctx, signal, handle) {
  try {
    for await (const event of ctx.event.subscribe({ signal })) {
      try {
        await handle(event)
      } catch (error) {
        logHookError(`event.${event?.type || "unknown"}`, error)
      }
    }
  } catch (error) {
    if (signal.aborted) return
    logHookError("event.subscribe", error)
  }
}

export async function prepareV2Prompt(event, {
  directory,
  recall,
  sessionInject,
  recallEnabled,
}) {
  const sessionID = event?.sessionID
  const messageID = event?.messageID
  if (!sessionID || !messageID) return
  const text = event?.prompt?.text
  const parts = typeof text === "string" && text.trim() ? [{ type: "text", text }] : []
  const input = { sessionID, messageID, directory }
  const blocks = []
  try {
    const sessionBlock = await sessionInject.buildSessionContext(input)
    if (sessionBlock) blocks.push(sessionBlock)
  } catch (error) {
    logHookError("session.prompt.profile", error)
  }
  if (recallEnabled) {
    try {
      const recallBlock = await recall.buildRelevantMemories(input, parts)
      if (recallBlock) blocks.push(recallBlock)
    } catch (error) {
      logHookError("session.prompt.recall", error)
    }
  }
  if (blocks.length === 0) return

  event.metadata = event.metadata && typeof event.metadata === "object" ? event.metadata : {}
  event.metadata[METADATA_KEY] = { context: blocks }
}

export function injectV2Context(event, repoContext) {
  const repoPrompt = repoContext.getRepoSystemPrompt()
  if (repoPrompt) pushSystem(event, repoPrompt)
  if (!Array.isArray(event?.messages)) return

  for (const message of event.messages) {
    const blocks = message?.metadata?.[METADATA_KEY]?.context
    if (message?.role !== "user" || !Array.isArray(blocks) || blocks.length === 0) continue
    if (!Array.isArray(message.content)) message.content = []
    if (message.content.some((part) => part?.metadata?.[METADATA_KEY] === true)) continue
    message.content.unshift({
      type: "text",
      text: blocks.filter((block) => typeof block === "string" && block).join("\n\n"),
      metadata: { [METADATA_KEY]: true },
    })
  }
}

// The plugin event stream carries every location served by the process, and
// execution events have no envelope location, so ownership comes from the
// session itself. An unresolvable session is handled rather than dropped.
async function ownsSession(ctx, ownership, event, sessionID) {
  if (event.type === "session.created") {
    const owned = sameLocation(event.location ?? event.data?.location, ctx.location)
    ownership.set(sessionID, owned)
    return owned
  }
  if (event.location) return sameLocation(event.location, ctx.location)
  if (ownership.has(sessionID)) return ownership.get(sessionID)
  if (event.type === "session.deleted" || !ctx.session?.get) return true
  try {
    const result = await ctx.session.get({ sessionID })
    const location = (result?.data ?? result)?.location
    if (location?.directory) {
      const owned = sameLocation(location, ctx.location)
      ownership.set(sessionID, owned)
      return owned
    }
  } catch (error) {
    logHookError("session.get", error)
  }
  return true
}

function sameLocation(ref, location) {
  if (!ref?.directory || !location?.directory) return true
  if (ref.directory !== location.directory) return false
  return !ref.workspaceID || !location.workspaceID || ref.workspaceID === location.workspaceID
}

export async function captureV2Context(ctx, sessionManager, cursors, sessionID) {
  const messages = await ctx.session.context({ sessionID })
  if (!Array.isArray(messages)) return
  // The cursor lives in host storage because the session state file is shared
  // by every location instance and cannot prove what was already captured.
  const cursor = cursors.get(sessionID) ?? await readCursor(ctx, sessionID)
  const cursorIndex = cursor ? messages.findIndex((message) => message?.id === cursor) : -1
  const pending = cursorIndex >= 0 ? messages.slice(cursorIndex + 1) : messages
  for (const message of pending) {
    for (const normalized of contextMessageEvents(sessionID, message)) {
      await sessionManager.handleEvent(normalized)
    }
  }
  const last = messages.at(-1)?.id
  if (!last) return
  cursors.set(sessionID, last)
  if (last !== cursor) await writeCursor(ctx, sessionID, last)
}

async function readCursor(ctx, sessionID) {
  if (!ctx?.storage?.get) return undefined
  try {
    const value = await ctx.storage.get(CURSOR_KEY_PREFIX + sessionID)
    return typeof value === "string" ? value : undefined
  } catch (error) {
    logHookError("storage.get", error)
    return undefined
  }
}

async function writeCursor(ctx, sessionID, cursor) {
  if (!ctx?.storage?.set) return
  try {
    await ctx.storage.set(CURSOR_KEY_PREFIX + sessionID, cursor)
  } catch (error) {
    logHookError("storage.set", error)
  }
}

async function forgetCursor(ctx, cursors, sessionID) {
  cursors.delete(sessionID)
  if (!ctx?.storage?.remove) return
  try {
    await ctx.storage.remove(CURSOR_KEY_PREFIX + sessionID)
  } catch (error) {
    logHookError("storage.remove", error)
  }
}

function isExecutionBoundary(type) {
  return type === "session.execution.succeeded" ||
    type === "session.execution.failed" ||
    type === "session.execution.interrupted"
}

function pushSystem(event, text) {
  if (!text) return
  if (!Array.isArray(event.system)) event.system = []
  if (event.system.some((part) => part?.type === "text" && part.text === text)) return
  event.system.push({ type: "text", text })
}

function logHookError(hook, error) {
  log("WARN", "v2", `OpenCode v2 ${hook} failed`, {
    error: error?.message ?? String(error),
  })
}

async function noticeV2Tool(event, vikingUriNotice) {
  if (event?.status !== "completed") return
  const before = toolResultText(event.result)
  const output = { output: before }
  await vikingUriNotice({ tool: event.tool, args: event.input }, output)
  if (!output.output || output.output === before) return
  writeToolResultText(event.result, output.output)
}

function toolResultText(result) {
  if (!result || typeof result !== "object") return ""
  if (typeof result.content === "string") return result.content
  if (!Array.isArray(result.content)) return ""
  return result.content
    .filter((item) => item?.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n\n")
}

function writeToolResultText(result, text) {
  if (!result || typeof result !== "object") return
  if (typeof result.content === "string" || result.content == null) {
    result.content = text
    return
  }
  if (!Array.isArray(result.content)) return
  // Preserve the original text/media blocks and append only the new notice.
  const before = toolResultText(result)
  const notice = text.startsWith(before) ? text.slice(before.length).trimStart() : text
  if (notice) result.content.push({ type: "text", text: notice })
}
