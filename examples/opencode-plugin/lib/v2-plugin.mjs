import { isRecallEnabled } from "./shared/recall-core.mjs"
import { createOpenVikingV2McpConfig } from "./mcp-config.mjs"
import { normalizeV2Event, userPromptEvents } from "./v2-events.mjs"
import { log } from "./utils.mjs"

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
  const userMessageIds = new Set()
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
      await vikingUriGuard(
        { tool: event.tool, args: event.input },
        { args: event.input },
      )
    })
    await ctx.tool.hook("execute.after", async (event) => {
      await noticeV2Tool(event, vikingUriNotice)
    })
  }

  if (ctx?.session?.hook) {
    await ctx.session.hook("prompt", async (event) => {
      const captured = userPromptEvents({
        sessionID: event?.sessionID,
        messageID: event?.messageID,
        text: event?.prompt?.text,
      })
      if (captured.length > 0 && event?.messageID) userMessageIds.add(event.messageID)
      for (const normalized of captured) {
        await sessionManager.handleEvent(normalized)
      }
    })

    await ctx.session.hook("context", async (event) => {
      await injectV2Context(event, {
        directory,
        recall,
        sessionInject,
        repoContext,
        recallEnabled: isRecallEnabled(config),
      })
    })

    await ctx.session.hook("compaction", async (event) => {
      log("INFO", "compaction", "OpenCode v2 session compacting", {
        opencode_session: event?.sessionID,
      })
      await sessionManager.flushSession(event?.sessionID, {
        commit: true,
        reason: "session.compaction",
      })
    })
  }

  const controller = new AbortController()
  if (ctx?.event?.subscribe) {
    void consumeEvents(ctx, controller.signal, async (event) => {
      if (event?.type === "session.message.content.updated" && userMessageIds.has(event.data?.messageID)) {
        return
      }
      for (const normalized of normalizeV2Event(event)) {
        await sessionManager.handleEvent(normalized)
        if (normalized.type === "session.created") {
          await repoContext.refreshRepos({ force: true })
        }
      }
    })
  }

  return async () => {
    controller.abort()
    await sessionManager.flushAll({ commit: true })
    log("INFO", "plugin", "OpenViking plugin disposed")
  }
}

async function consumeEvents(ctx, signal, handle) {
  try {
    for await (const event of ctx.event.subscribe({ signal })) {
      await handle(event)
    }
  } catch (error) {
    if (signal.aborted) return
    log("WARN", "event", "OpenCode v2 event subscription ended", {
      error: error?.message ?? String(error),
    })
  }
}

export async function injectV2Context(event, {
  directory,
  recall,
  sessionInject,
  repoContext,
  recallEnabled,
}) {
  const sessionID = event?.sessionID
  if (!sessionID) return
  const prompt = repoContext.getRepoSystemPrompt()
  if (prompt) pushSystem(event, prompt)

  const query = latestUserText(event.messages)
  const messageID = `ov-${sessionID}`
  const output = {
    parts: query ? [{ type: "text", text: query }] : [],
    message: { sessionID, id: messageID },
  }
  const input = { sessionID, messageID, directory }
  try {
    await sessionInject.injectSessionContext(input, output)
    if (recallEnabled) await recall.injectRelevantMemories(input, output)
  } catch (error) {
    log("WARN", "recall", "Auto recall failed", { error: error?.message ?? String(error) })
    return
  }
  for (const part of output.parts) {
    if (part?.synthetic && part.text) pushSystem(event, part.text)
  }
}

function pushSystem(event, text) {
  if (!text) return
  if (!Array.isArray(event.system)) event.system = []
  event.system.push({ type: "text", text })
}

export function latestUserText(messages) {
  if (!Array.isArray(messages)) return ""
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    const role = message?.role
    if (role && role !== "user") continue
    const text = messageText(message)
    if (text) return text
  }
  return ""
}

function messageText(message) {
  if (!message || typeof message !== "object") return ""
  if (typeof message.content === "string") return message.content.trim()
  const blocks = Array.isArray(message.content)
    ? message.content
    : Array.isArray(message.parts)
      ? message.parts
      : []
  return blocks
    .filter((block) => block && (block.type === "text" || block.type === undefined) && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n")
    .trim()
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
  const existing = result.content.find((item) => item?.type === "text" && typeof item.text === "string")
  if (existing) existing.text = text
  else result.content.push({ type: "text", text })
}
