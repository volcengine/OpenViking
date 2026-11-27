import { readFile, readdir } from "node:fs/promises"
import { join } from "node:path"
import { createOpencodeHost } from "../host.mjs"
import { loadConfig, resolveDataDir } from "./config.mjs"
import { createOpenVikingV2McpConfig, openVikingSkillsDir } from "./mcp-config.mjs"
import { createRepoContext } from "./repo-context.mjs"
import { runStage, toolCall } from "./shared/flow/index.mjs"
import { initLogger, log } from "./utils.mjs"

const METADATA_KEY = "openviking"
// Where the 0.5 plugin kept its capture cursor; read until the flow stores its own.
const CURSOR_KEY_PREFIX = "capture-cursor/"
const EXECUTION_ENDS = new Set([
  "session.execution.succeeded",
  "session.execution.failed",
  "session.execution.interrupted",
])

export async function startV2Plugin(ctx, { pluginRoot }) {
  const directory = ctx?.location?.project?.directory || ctx?.location?.directory
  const config = loadConfig(pluginRoot, directory)
  const dataDir = resolveDataDir(pluginRoot, config)
  initLogger(dataDir)
  if (!config.enabled) {
    log("INFO", "plugin", "OpenViking plugin is disabled in configuration")
    return undefined
  }
  const host = createOpencodeHost({ dataDir, mcpEnabled: config.mcp.enabled })
  const repoContext = createRepoContext({ config })
  const sessions = new Map()

  // What a session is: its parent (a subagent is captured under it) and its
  // location. Looked up once; nothing here decides what is captured.
  async function sessionInfo(sessionID, event) {
    if (sessions.has(sessionID)) return sessions.get(sessionID)
    let data = event?.type === "session.created" ? event.data : null
    if (!data && ctx.session?.get) {
      try {
        const result = await ctx.session.get({ sessionID })
        data = result?.data ?? result
      } catch (error) {
        logHookError("session.get", error)
      }
    }
    const info = { parentID: data?.parentID ?? data?.parentId ?? "", location: event?.location ?? data?.location }
    if (data) sessions.set(sessionID, info)
    return info
  }

  async function payloadFor(sessionID, extra = {}) {
    const { parentID } = await sessionInfo(sessionID)
    return { sessionID, parentID, directory, ...extra }
  }

  async function captureStage(event, sessionID) {
    let messages = []
    try {
      const context = await ctx.session.context({ sessionID })
      if (Array.isArray(context)) messages = context
    } catch (error) {
      logHookError(`${event}.context`, error)
    }
    const storedCursor = await storage("get", sessionID)
    await runStage(host, event, await payloadFor(sessionID, { messages, storedCursor }), { cfg: config })
  }

  async function storage(op, sessionID) {
    if (!ctx?.storage?.[op]) return undefined
    try {
      const value = await ctx.storage[op](CURSOR_KEY_PREFIX + sessionID)
      return typeof value === "string" ? value : undefined
    } catch (error) {
      logHookError(`storage.${op}`, error)
      return undefined
    }
  }

  if (config.mcp.enabled && ctx?.mcp?.transform) {
    let registered = false
    await ctx.mcp.transform((editor) => {
      registered = editor.get?.("openviking")?.disabled !== true
      if (registered) editor.set("openviking", createOpenVikingV2McpConfig(pluginRoot))
    })
    log("INFO", "mcp", "Registered OpenViking MCP server for OpenCode v2")
    if (ctx?.skill?.transform) {
      const skills = await loadBundledSkills(openVikingSkillsDir(pluginRoot))
      // Transforms run again after setup, so read the MCP decision on each run.
      await ctx.skill.transform((editor) => {
        if (!registered) return
        // A user skill overrides a bundled one by ID or by name; the host keys
        // skills by ID only, so a same-name skill would otherwise be listed twice.
        const taken = new Set(editor.list().flatMap((skill) => [skill.id, skill.name]))
        for (const skill of skills) if (!taken.has(skill.id) && !taken.has(skill.name)) editor.add(skill)
      })
    }
  } else if (!config.mcp.enabled) {
    log("INFO", "mcp", "Skipped bundled MCP registration in hook-only mode")
  }

  if (ctx?.tool?.hook) {
    await ctx.tool.hook("execute.before", async (event) => {
      const output = toolCall(host, await payloadFor(event.sessionID, { tool: event.tool, input: event.input }), "execute.before")
      // The host reads the arguments back by reference.
      if (output.input && event.input && typeof event.input === "object") Object.assign(event.input, output.input)
      if (output.deny) {
        log("INFO", "viking-uri-guard", "Blocked filesystem tool for viking URI", { tool: event.tool })
        throw new Error(output.deny)
      }
    })
    await ctx.tool.hook("execute.after", async (event) => {
      try {
        if (event?.status !== "completed") return
        const { notice } = toolCall(host, { sessionID: event.sessionID, tool: event.tool, input: event.input }, "execute.before")
        if (notice) appendToolResultText(event.result, notice)
      } catch (error) {
        logHookError("tool.execute.after", error)
      }
    })
  }

  if (ctx?.session?.hook) {
    await ctx.session.hook("prompt", async (event) => {
      try {
        if (!event?.sessionID || !event?.messageID) return
        const payload = await payloadFor(event.sessionID, { messageID: event.messageID, prompt: event.prompt?.text })
        const { context } = await runStage(host, "prompt", payload, { cfg: config })
        if (!context) return
        event.metadata = event.metadata && typeof event.metadata === "object" ? event.metadata : {}
        event.metadata[METADATA_KEY] = { context: [context] }
      } catch (error) {
        logHookError("session.prompt", error)
      }
    })

    await ctx.session.hook("context", async (event) => {
      try {
        injectV2Context(event, repoContext)
      } catch (error) {
        logHookError("session.context", error)
      }
    })

    await ctx.session.hook("compaction", async (event) => {
      try {
        // context() drops the old transcript as soon as compaction completes.
        await captureStage("compaction", event.sessionID)
      } catch (error) {
        logHookError("session.compaction", error)
      }
    })
  }

  repoContext.refreshRepos({ force: true }).catch(() => {})

  const controller = new AbortController()
  let eventTask = Promise.resolve()
  if (ctx?.event?.subscribe) {
    eventTask = consumeEvents(ctx, controller.signal, async (event) => {
      const sessionID = event?.data?.sessionID ?? event?.data?.sessionId
      if (!sessionID) return
      const ended = EXECUTION_ENDS.has(event.type)
      if (!ended && event.type !== "session.created" && event.type !== "session.deleted") return
      if (!(await ownsSession(event, sessionID))) return
      if (event.type === "session.execution.failed") {
        log("WARN", "v2", "OpenCode v2 execution failed", {
          opencode_session: sessionID,
          error: event.data?.error?.message ?? event.data?.error,
        })
      }
      if (event.type === "session.created") {
        await runStage(host, "session.created", await payloadFor(sessionID), { cfg: config })
        await repoContext.refreshRepos({ force: true })
      } else if (ended) {
        await captureStage("execution.ended", sessionID)
      } else {
        await captureStage("session.deleted", sessionID)
        sessions.delete(sessionID)
        await storage("remove", sessionID)
      }
    })
  }

  // The plugin event stream carries every location served by the process, and
  // execution events have no envelope location, so ownership comes from the
  // session itself. An unresolvable session is handled rather than dropped.
  async function ownsSession(event, sessionID) {
    if (event.location) return sameLocation(event.location, ctx.location)
    if (event.type === "session.deleted" && !sessions.has(sessionID)) return true
    const info = await sessionInfo(sessionID, event)
    return sameLocation(info.location, ctx.location)
  }

  return async () => {
    controller.abort()
    await eventTask.catch((error) => logHookError("plugin.cleanup", error))
    log("INFO", "plugin", "OpenViking plugin disposed")
  }
}

async function loadBundledSkills(root) {
  const entries = await readdir(root, { withFileTypes: true }).catch((error) => {
    logHookError("skill.load", error)
    return []
  })
  const skills = []
  for (const entry of entries.filter((item) => item.isDirectory())) {
    const path = join(root, entry.name, "SKILL.md")
    try {
      const text = await readFile(path, "utf8")
      const frontmatter = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/)
      // Bundled descriptions are a plain scalar or a folded (`>`) block.
      const [, head = "", folded = ""] = frontmatter?.[1].match(/^description:(.*)((?:\r?\n[ \t]+.*)*)/m) ?? []
      const description = (/^\s*>-?\s*$/.test(head) ? folded : head).trim().replace(/\s+/g, " ")
      if (!description) throw new Error(`no description in ${path}`)
      skills.push({ id: entry.name, name: entry.name, description, path, content: text.slice(frontmatter[0].length) })
    } catch (error) {
      logHookError("skill.load", error)
    }
  }
  return skills
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

const START_CONTEXT = /<openviking-context source="session-start">\n[\s\S]*?\n<\/openviking-context>\n?/

// A compaction can keep the message that carried the start context (its recent
// tail, an overflow replay), so a request keeps only the newest copy.
export function keepNewestStartContext(partLists) {
  const carriers = partLists.flatMap((parts) => (Array.isArray(parts) ? parts : [])
    .filter((part) => typeof part?.text === "string" && START_CONTEXT.test(part.text))
    .map((part) => [parts, part]))
  for (const [parts, part] of carriers.slice(0, -1)) {
    part.text = part.text.replace(START_CONTEXT, "")
    if (!part.text.trim()) parts.splice(parts.indexOf(part), 1)
  }
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
  keepNewestStartContext(event.messages.map((message) => message.content))
}

function sameLocation(ref, location) {
  if (!ref?.directory || !location?.directory) return true
  if (ref.directory !== location.directory) return false
  return !ref.workspaceID || !location.workspaceID || ref.workspaceID === location.workspaceID
}

function pushSystem(event, text) {
  if (!Array.isArray(event.system)) event.system = []
  if (event.system.some((part) => part?.type === "text" && part.text === text)) return
  event.system.push({ type: "text", text })
}

function logHookError(hook, error) {
  log("WARN", "v2", `OpenCode v2 ${hook} failed`, {
    error: error?.message ?? String(error),
  })
}

function appendToolResultText(result, notice) {
  if (!result || typeof result !== "object") return
  if (typeof result.content === "string" || result.content == null) {
    result.content = result.content ? `${result.content}\n\n${notice}` : notice
    return
  }
  // The original text and media blocks stay; the notice is a block of its own.
  if (Array.isArray(result.content)) result.content.push({ type: "text", text: notice })
}
