import { tool } from "@opencode-ai/plugin"
import { addMemaddResource } from "./memadd-local.mjs"
import {
  log,
  makeRequest,
  unwrapResponse,
  validateVikingUri,
} from "./utils.mjs"

const z = tool.schema

export function createMemoryTools({ config, sessionManager, projectDirectory }) {
  return {
    memsearch: tool({
      description:
        "Search OpenViking memories, indexed repositories, and skills. Use this for semantic or conceptual questions. Narrow `target_uri` whenever possible, for example viking://resources/project/ or viking://user/memories/.",
      args: {
        query: z.string().describe("Natural language query, question, or task description."),
        target_uri: z.string().optional().describe("Optional Viking URI scope, e.g. viking://resources/ or viking://user/memories/."),
        mode: z.enum(["auto", "fast", "deep"]).optional().describe("auto chooses based on query complexity; fast uses /find; deep uses /search with session context when available."),
        session_id: z.string().optional().describe("Optional explicit OpenViking session ID for context-aware search."),
        limit: z.number().optional().describe("Maximum number of results. Defaults to 10."),
        score_threshold: z.number().optional().describe("Optional minimum score threshold."),
      },
      async execute(args, context) {
        try {
          let sessionId = args.session_id
          if (!sessionId && context.sessionID) {
            sessionId = sessionManager.getMappedSessionId(context.sessionID)
          }

          const mode = resolveSearchMode(args.mode, args.query, sessionId)
          const body = {
            query: args.query,
            limit: args.limit ?? 10,
          }
          if (args.target_uri) body.target_uri = args.target_uri
          if (args.score_threshold !== undefined) body.score_threshold = args.score_threshold
          if (mode === "deep" && sessionId) body.session_id = sessionId

          const response = await makeRequest(config, {
            method: "POST",
            endpoint: mode === "deep" ? "/api/v1/search/search" : "/api/v1/search/find",
            body,
            abortSignal: context.abort,
          })
          return formatSearchResults(unwrapResponse(response), args.query, { mode })
        } catch (error) {
          log("ERROR", "memsearch", "Search failed", { error: error?.message, args })
          return `Error: ${error.message}`
        }
      },
    }),

    memread: tool({
      description:
        "Read a specific viking:// URI. Use after memsearch, membrowse, memgrep, or memglob returns a URI. `auto` chooses overview for directories and read for files.",
      args: {
        uri: z.string().describe("Complete Viking URI to read."),
        level: z.enum(["auto", "abstract", "overview", "read"]).optional().describe("Read level. Defaults to auto."),
      },
      async execute(args, context) {
        const validationError = validateVikingUri(args.uri, "memread")
        if (validationError) return validationError

        try {
          let level = args.level ?? "auto"
          if (level === "auto") {
            level = await resolveReadLevel(config, args.uri, context.abort)
          }
          const response = await makeRequest(config, {
            method: "GET",
            endpoint: `/api/v1/content/${level}?uri=${encodeURIComponent(args.uri)}`,
            abortSignal: context.abort,
          })
          const content = unwrapResponse(response)
          return typeof content === "string" ? content : JSON.stringify(content, null, 2)
        } catch (error) {
          log("ERROR", "memread", "Read failed", { error: error?.message, uri: args.uri })
          return `Error: ${error.message}`
        }
      },
    }),

    membrowse: tool({
      description:
        "Browse OpenViking filesystem structure. Use list/tree/stat to discover exact URIs before reading. Scope to the narrowest useful viking:// path.",
      args: {
        uri: z.string().describe("Viking URI to inspect, e.g. viking://resources/ or viking://user/memories/."),
        view: z.enum(["list", "tree", "stat"]).optional().describe("Browse view. Defaults to list."),
        recursive: z.boolean().optional().describe("For list view only, recursively list descendants."),
        simple: z.boolean().optional().describe("For list view only, return simpler URI-oriented output."),
      },
      async execute(args, context) {
        const validationError = validateVikingUri(args.uri, "membrowse")
        if (validationError) return validationError

        try {
          const view = args.view ?? "list"
          const encodedUri = encodeURIComponent(args.uri)
          let endpoint
          if (view === "stat") {
            endpoint = `/api/v1/fs/stat?uri=${encodedUri}`
          } else if (view === "tree") {
            endpoint = `/api/v1/fs/tree?uri=${encodedUri}`
          } else {
            endpoint = `/api/v1/fs/ls?uri=${encodedUri}&recursive=${args.recursive ? "true" : "false"}&simple=${args.simple ? "true" : "false"}`
          }
          const response = await makeRequest(config, { method: "GET", endpoint, abortSignal: context.abort })
          return JSON.stringify({ view, result: unwrapResponse(response) }, null, 2)
        } catch (error) {
          log("ERROR", "membrowse", "Browse failed", { error: error?.message, uri: args.uri })
          return `Error: ${error.message}`
        }
      },
    }),

    memcommit: tool({
      description:
        "Commit the current OpenCode session to OpenViking and extract persistent memories. Use for immediate memory extraction before ending a conversation or after important preferences/decisions are discussed.",
      args: {
        session_id: z.string().optional().describe("Optional explicit OpenViking session ID. Omit to use the current OpenCode session mapping."),
      },
      async execute(args, context) {
        const sessionId = args.session_id ?? (context.sessionID ? sessionManager.getMappedSessionId(context.sessionID) : undefined)
        if (!sessionId) {
          return "Error: No OpenViking session is associated with the current OpenCode session. Start or resume a normal OpenCode session first, or pass session_id."
        }

        try {
          const result = await sessionManager.commitSession(sessionId, context.sessionID, context.abort)
          return formatCommitResult(sessionId, result)
        } catch (error) {
          log("ERROR", "memcommit", "Commit failed", { error: error?.message, session_id: sessionId })
          return `Error: ${error.message}`
        }
      },
    }),

    memgrep: tool({
      description:
        "Search exact text or regex-like patterns in OpenViking content. Use this for symbols, function names, classes, error strings, or known keywords. Narrow `uri` to the smallest relevant repository or directory.",
      args: {
        pattern: z.string().describe("Pattern or exact keyword to search for."),
        uri: z.string().optional().describe("Starting Viking URI. Defaults to viking://resources/."),
        case_insensitive: z.boolean().optional().describe("Whether search should ignore case."),
        exclude_uri: z.string().optional().describe("Optional URI prefix to exclude from matches."),
        level_limit: z.number().optional().describe("Optional maximum traversal depth."),
      },
      async execute(args, context) {
        const uri = args.uri ?? "viking://resources/"
        const validationError = validateVikingUri(uri, "memgrep")
        if (validationError) return validationError

        try {
          const body = { uri, pattern: args.pattern }
          if (args.case_insensitive !== undefined) body.case_insensitive = args.case_insensitive
          if (args.exclude_uri) body.exclude_uri = args.exclude_uri
          if (args.level_limit !== undefined) body.level_limit = args.level_limit
          const response = await makeRequest(config, {
            method: "POST",
            endpoint: "/api/v1/search/grep",
            body,
            abortSignal: context.abort,
          })
          return JSON.stringify(unwrapResponse(response), null, 2)
        } catch (error) {
          log("ERROR", "memgrep", "Grep failed", { error: error?.message, args })
          return `Error: ${error.message}`
        }
      },
    }),

    memglob: tool({
      description:
        "List files by glob pattern in OpenViking. Use this to enumerate candidate files before memread. Narrow `uri` to the smallest relevant repository or directory.",
      args: {
        pattern: z.string().describe("Glob pattern, e.g. **/*.py or **/test_*.ts."),
        uri: z.string().optional().describe("Starting Viking URI. Defaults to viking://resources/."),
        node_limit: z.number().optional().describe("Optional maximum number of matches."),
      },
      async execute(args, context) {
        const uri = args.uri ?? "viking://resources/"
        const validationError = validateVikingUri(uri, "memglob")
        if (validationError) return validationError

        try {
          const body = { uri, pattern: args.pattern }
          if (args.node_limit !== undefined) body.node_limit = args.node_limit
          const response = await makeRequest(config, {
            method: "POST",
            endpoint: "/api/v1/search/glob",
            body,
            abortSignal: context.abort,
          })
          return JSON.stringify(unwrapResponse(response), null, 2)
        } catch (error) {
          log("ERROR", "memglob", "Glob failed", { error: error?.message, args })
          return `Error: ${error.message}`
        }
      },
    }),

    memadd: tool({
      description:
        "Add a remote URL or local file resource to OpenViking under viking://resources/. Local files are uploaded through OpenViking temp upload before indexing. After adding, this returns observer queue status so indexing progress is visible.",
      args: {
        path: z.string().describe("Remote http(s) URL, local file path, or file:// URL to add. Relative local paths are resolved from the OpenCode project directory."),
        to: z.string().optional().describe("Exact target URI under viking://resources/. Cannot be used with parent."),
        parent: z.string().optional().describe("Parent URI under viking://resources/. Cannot be used with to."),
        reason: z.string().optional().describe("Reason for adding this resource."),
        instruction: z.string().optional().describe("Optional processing instruction."),
        wait: z.boolean().optional().describe("Whether OpenViking should wait for semantic processing."),
        timeout: z.number().optional().describe("Timeout seconds when wait=true."),
        watch_interval: z.number().optional().describe("Minutes between scheduled refreshes. Requires to."),
      },
      async execute(args, context) {
        if (args.to && args.parent) return "Error: Use either `to` or `parent`, not both."
        if (args.to && !args.to.startsWith("viking://resources")) return "Error: `to` must be under viking://resources/."
        if (args.parent && !args.parent.startsWith("viking://resources")) return "Error: `parent` must be under viking://resources/."

        try {
          const result = await addMemaddResource(config, args, projectDirectory, context.abort)
          if (result.error) return result.error
          const queue = await getQueueStatus(config, context.abort)
          return JSON.stringify({ add_resource: unwrapResponse(result.addResponse), queue }, null, 2)
        } catch (error) {
          log("ERROR", "memadd", "Add resource failed", { error: error?.message, args })
          return `Error: ${error.message}`
        }
      },
    }),

    memremove: tool({
      description:
        "Remove a viking:// resource. The user must explicitly confirm deletion before this tool is called. Set confirm=true, otherwise deletion is refused.",
      args: {
        uri: z.string().describe("Viking URI to remove."),
        recursive: z.boolean().optional().describe("Recursively remove a directory."),
        confirm: z.boolean().describe("Must be true after explicit user confirmation."),
      },
      async execute(args, context) {
        if (!args.confirm) {
          return "Error: Refusing to delete. Ask the user for explicit confirmation, then call memremove with confirm=true."
        }
        const validationError = validateVikingUri(args.uri, "memremove")
        if (validationError) return validationError

        try {
          const response = await makeRequest(config, {
            method: "DELETE",
            endpoint: `/api/v1/fs?uri=${encodeURIComponent(args.uri)}&recursive=${args.recursive ? "true" : "false"}`,
            abortSignal: context.abort,
          })
          return JSON.stringify(unwrapResponse(response), null, 2)
        } catch (error) {
          log("ERROR", "memremove", "Remove failed", { error: error?.message, args })
          return `Error: ${error.message}`
        }
      },
    }),

    memqueue: tool({
      description: "Return OpenViking observer queue status for embedding and semantic processing after resource indexing operations.",
      args: {},
      async execute(_args, context) {
        try {
          const queue = await getQueueStatus(config, context.abort)
          return JSON.stringify(queue, null, 2)
        } catch (error) {
          log("ERROR", "memqueue", "Queue status failed", { error: error?.message })
          return `Error: ${error.message}`
        }
      },
    }),
  }
}

async function resolveReadLevel(config, uri, abortSignal) {
  try {
    const statResponse = await makeRequest(config, {
      method: "GET",
      endpoint: `/api/v1/fs/stat?uri=${encodeURIComponent(uri)}`,
      abortSignal,
    })
    return unwrapResponse(statResponse)?.isDir ? "overview" : "read"
  } catch {
    return "read"
  }
}

function resolveSearchMode(requestedMode, query, sessionId) {
  if (requestedMode === "fast" || requestedMode === "deep") return requestedMode
  if (sessionId) return "deep"
  const normalized = query.trim()
  const wordCount = normalized ? normalized.split(/\s+/).length : 0
  return normalized.includes("?") || normalized.length >= 80 || wordCount >= 8 ? "deep" : "fast"
}

function formatSearchResults(result, query, extra) {
  const memories = result?.memories ?? []
  const resources = result?.resources ?? []
  const skills = result?.skills ?? []
  const allResults = [...memories, ...resources, ...skills]
  if (allResults.length === 0) {
    return "No results found matching the query."
  }
  return JSON.stringify(
    {
      total: result?.total ?? allResults.length,
      memories,
      resources,
      skills,
      query_plan: result?.query_plan,
      query,
      ...extra,
    },
    null,
    2,
  )
}

function formatCommitResult(sessionId, result) {
  const task = result.task
  const payload = task?.result ?? result.result ?? {}
  const memoriesExtracted = totalMemoriesExtracted(payload.memories_extracted)
  return JSON.stringify(
    {
      message: result.status === "accepted" ? "Commit is still processing in the background" : `Memory extraction complete: ${memoriesExtracted} memories extracted`,
      session_id: payload.session_id ?? sessionId,
      status: result.status,
      memories_extracted: memoriesExtracted,
      archived: payload.archived ?? false,
      task_id: task?.task_id ?? result.task_id,
    },
    null,
    2,
  )
}

function totalMemoriesExtracted(memories) {
  if (typeof memories === "number") return memories
  if (!memories || typeof memories !== "object") return 0
  return Object.entries(memories).reduce((sum, [key, value]) => {
    if (key === "total") return sum
    return sum + (typeof value === "number" ? value : 0)
  }, 0)
}

async function getQueueStatus(config, abortSignal) {
  const response = await makeRequest(config, {
    method: "GET",
    endpoint: "/api/v1/observer/queue",
    abortSignal,
    timeoutMs: 5000,
  })
  return unwrapResponse(response)
}
