import { tool } from "@opencode-ai/plugin"
import { effectivePeerId, log, makeRequest, unwrapResponse, validateVikingUri } from "./utils.mjs"

const z = tool.schema

const CODE_TOOL_ENDPOINTS = {
  locate: "/api/v1/code/locate",
  search: "/api/v1/code/search",
  outline: "/api/v1/code/outline",
  expand: "/api/v1/code/expand",
}

function codeToolRequestOptions(
  kind,
  { uri, source, query, terms, hints, failingTests, symbol, actorPeerId, abortSignal },
) {
  const body = kind === "locate" ? { source } : { uri }
  if (kind === "search") body.query = query
  if (kind === "locate") {
    body.query = query
    body.terms = terms ?? []
    body.hints = hints ?? {}
    body.failing_tests = failingTests ?? []
    body.output_format = "json"
    body.debug = false
  }
  if (kind === "expand") body.symbol = symbol

  return {
    method: "POST",
    endpoint: CODE_TOOL_ENDPOINTS[kind],
    body,
    abortSignal,
    actorPeerId,
  }
}

function splitCodeSearchBlocks(result) {
  const lines = String(result ?? "").split("\n")
  const header = lines.shift() ?? ""
  const blocks = []
  let current = []
  for (const line of lines) {
    if (!line.trim() && current.length) {
      blocks.push(current)
      current = []
      continue
    }
    if (line.trim() || current.length) current.push(line)
  }
  if (current.length) blocks.push(current)
  return { header, blocks }
}

function parseTotalMatches(header, fallback) {
  const match = String(header).match(/^(\d+)\s+code matches\b/)
  return match ? Number(match[1]) : fallback
}

function localPathForUri(fileUri, rootUri) {
  const normalizedRoot = String(rootUri ?? "").replace(/\/+$/, "")
  if (!normalizedRoot || !fileUri.startsWith(`${normalizedRoot}/`)) return null
  return `./${fileUri.slice(normalizedRoot.length + 1)}`
}

function limitContentLines(block, maxContentLines) {
  const out = []
  let inContent = false
  let contentCount = 0
  for (const line of block) {
    if (line.trim() === "content:") {
      inContent = true
      contentCount = 0
      out.push(line)
      continue
    }
    if (inContent && line.match(/^\s+L\d+:/)) {
      contentCount += 1
      if (contentCount <= maxContentLines) out.push(line)
      continue
    }
    out.push(line)
  }
  return out
}

export function formatCodeSearchOutput(
  result,
  { uri, maxFiles = 5, maxContentLines = 3 } = {},
) {
  if (typeof result !== "string" || !result.includes("code matches for")) return result

  const { header, blocks } = splitCodeSearchBlocks(result)
  const total = parseTotalMatches(header, blocks.length)
  const limitedBlocks = blocks.slice(0, maxFiles)
  const out = [
    total > limitedBlocks.length
      ? header.replace(
          /^\d+\s+code matches/,
          `Showing top ${limitedBlocks.length} of ${total} code matches`,
        )
      : header,
  ]

  for (const block of limitedBlocks) {
    if (!block.length) continue
    const fileUri = block[0]
    out.push("")
    out.push(fileUri)
    const localPath = localPathForUri(fileUri, uri)
    if (localPath) out.push(`  local: ${localPath}`)
    out.push(...limitContentLines(block.slice(1), maxContentLines))
  }

  if (total > limitedBlocks.length) {
    out.push("")
    out.push(`(narrow uri or query to inspect ${total - limitedBlocks.length} more match(es))`)
  }
  return out.join("\n")
}

export function formatCodeLocateOutput(result, { uri } = {}) {
  if (result && typeof result === "object" && result.schema_version === "code-locate/v1") {
    const candidateLabel = (candidate) =>
      candidate?.location?.path ?? candidate?.location?.uri ?? candidate?.location?.relative_path
    const snippetLines = (candidate) =>
      (candidate?.snippets ?? []).map(
        (snippet) => `${candidateLabel(candidate)}:L${snippet.line} ${snippet.text}`,
      )
    const readWindow = (candidate, limit) => {
      const snippet = candidate?.snippets?.[0]
      if (!snippet) return null
      return `${candidateLabel(candidate)} offset=${snippet.line} limit=${limit}`
    }
    const symbolLabel = (symbol) => {
      if (!symbol?.range) return symbol?.name
      return `${symbol.name} L${symbol.range.start_line}-${symbol.range.end_line}`
    }
    const lines = []
    const editCandidates = (result.edit_candidates ?? []).slice(0, 3)
    const behaviorReferences = (result.behavior_references ?? []).slice(0, 2)
    const runnable = (result.verification ?? []).filter((item) => item.command)
    const stagedVerification =
      runnable.find((item) => item.kind === "static") ??
      runnable.find((item) => String(item.command).includes("py_compile")) ??
      runnable[0]
    const stagedCandidate = editCandidates.find((candidate) =>
      String(candidate.next_action ?? "").trim().startsWith("PATCH FIRST:"),
    )
    if (stagedCandidate) {
      const stagedAction = String(stagedCandidate.next_action).replace(/^PATCH FIRST:\s*/, "")
      lines.push("OpenViking staged action:")
      lines.push("- Classification: diagnostic wording or argument delta.")
      lines.push("- Completion criterion: patch the production diagnostic emitter and run the immediate static check.")
      lines.push("- Keep the first pass limited to the listed edit target and behavior reference.")
      lines.push("- Treat tests and assertions as behavior evidence unless the issue explicitly asks to update tests.")
      lines.push(`- Follow first: ${stagedAction}`)
      lines.push(`- Edit target: ${candidateLabel(stagedCandidate)}`)
      const editLines = snippetLines(stagedCandidate)
      if (editLines[0]) lines.push(`- Edit line: ${editLines[0]}`)
      for (const line of editLines.slice(1, 3)) {
        lines.push(`- Message shape line: ${line}`)
      }
      const windows = [readWindow(stagedCandidate, 4)].filter(Boolean)
      if (windows.length) {
        lines.push(`- Minimal read window if needed: ${windows.join("; ")}`)
      }
      lines.push("- Do not read a larger function or helper window before the first patch.")
      lines.push(
        "- Patch draft: update the production diagnostic wording, arguments, or guard indicated by the edit line; use nearby same-file diagnostics only as style evidence. Treat tests/assertions as behavior evidence, not patch targets.",
      )
      lines.push("- First patch contract: use the edit and message shape lines above to patch production code now.")
      lines.push("- Do not edit tests or assertions during this first patch unless the issue explicitly asks for test changes.")
      lines.push(
        "- If patch application fails, read the exact edit line and retry a minimal diagnostic patch before broadening.",
      )
      lines.push(
        "- If verification fails before test collection or during dependency imports, treat it as environment setup; do not broaden code search.",
      )
      lines.push("- After reading the listed edit target, edit and verify before extra read/grep/glob.")
      lines.push("- If the immediate static check passes, run only the narrow verification suggested by the result when available.")
      lines.push("- Do not expand symbol ranges or inspect adjacent implementation until the listed diagnostic path fails.")
      if (stagedVerification) {
        lines.push(
          `- Verify immediate path: ${
            stagedVerification.cwd ? `cd ${stagedVerification.cwd} && ` : ""
          }${
            stagedVerification.command
          }`,
        )
      }
      lines.push("- Delay broad grep/read/codesearch until this patch and immediate verification path fails.")
      return lines.join("\n")
    }
    if (result.summary_text) lines.push(`Summary: ${result.summary_text}`)
    lines.push(
      "Contract: read the top edit candidate first and the top behavior reference only if needed. Patch before broader grep/read/codesearch.",
    )
    lines.push(
      "If pytest fails before collection or dependency imports, treat it as setup and do not broaden code search.",
    )
    lines.push("Top edit candidates:")
    if (!editCandidates.length) lines.push("- no ranked candidates")
    for (const candidate of editCandidates) {
      lines.push(`${candidate.rank}. ${candidateLabel(candidate)}`)
      const focus = (candidate.focus_symbols ?? []).map(symbolLabel).filter(Boolean)
      if (focus.length) lines.push(`   focus: ${focus.join(", ")}`)
      const reasons = candidate.reasons ?? []
      if (reasons.length) lines.push(`   why: ${reasons.slice(0, 3).join("; ")}`)
      const snippets = candidate.snippets ?? []
      if (snippets.length) {
        lines.push(
          `   snippets: ${snippets
            .slice(0, 2)
            .map((snippet) => `L${snippet.line}: ${snippet.text}`)
            .join("; ")}`,
        )
      }
      if (candidate.next_action) lines.push(`   next: ${candidate.next_action}`)
    }

    lines.push("")
    lines.push("Useful behavior references:")
    if (!behaviorReferences.length) lines.push("- no ranked candidates")
    for (const candidate of behaviorReferences) {
      lines.push(`${candidate.rank}. ${candidateLabel(candidate)}`)
      const reasons = candidate.reasons ?? []
      if (reasons.length) lines.push(`   why: ${reasons.slice(0, 3).join("; ")}`)
      const snippets = candidate.snippets ?? []
      if (snippets.length) {
        lines.push(
          `   snippets: ${snippets
            .slice(0, 2)
            .map((snippet) => `L${snippet.line}: ${snippet.text}`)
            .join("; ")}`,
        )
      }
    }

    if (runnable.length) {
      lines.push("")
      lines.push("Suggested verification:")
      for (const item of runnable.slice(0, 2)) {
        lines.push(`- ${item.cwd ? `cd ${item.cwd} && ` : ""}${item.command}`)
      }
    }
    return lines.join("\n")
  }

  if (typeof result !== "string" || !result.includes("Likely edit locations:")) return result

  const out = []
  for (const line of result.split("\n")) {
    out.push(line)
    const match = line.match(/^(\d+\.\s+)?(viking:\/\/\S+)/)
    if (!match) continue
    const localPath = localPathForUri(match[2], uri)
    if (localPath) out.push(`   local: ${localPath}`)
  }
  return out.join("\n")
}

export function createCodeTools({ config, projectDirectory } = {}) {
  const actorPeerId = effectivePeerId(config)
  const tools = {
    codelocate: tool({
      description:
        "Rank likely edit files/symbols and useful test references for a concrete code query in the current local repository. " +
        "Use this before broad grep/read exploration when you have an issue statement, traceback, failing behavior, or code-location question. " +
        "Returns compact edit candidates, behavior references, reasons, local edit paths, and next actions without full source bodies. " +
        "If output contains `PATCH FIRST`, read only the listed target/reference, then edit and verify before broad grep/read/codesearch. " +
        "Prefer this over repeated codesearch calls for repository bug fixing; use codesearch for follow-up narrow terms only.",
      args: {
        query: z
          .string()
          .describe(
            "Issue statement, bug report, failing behavior, traceback, error message, or code-location question.",
          ),
        terms: z
          .array(z.string())
          .optional()
          .describe(
            "Optional 5-20 high-signal search terms. Preserve code identifiers; do not include generic words unless part of an exact message.",
          ),
        hints: z
          .object({
            paths: z
              .array(z.string())
              .optional()
              .describe("Explicit file paths or filenames mentioned in the issue."),
            path_terms: z
              .array(z.string())
              .optional()
              .describe("Directory or module concepts implied by the issue, not guessed concrete paths."),
            symbols: z
              .array(z.string())
              .optional()
              .describe("Class, function, method, or variable names mentioned in the issue."),
            imports: z
              .array(z.string())
              .optional()
              .describe("Package or module names mentioned in the issue."),
            errors: z
              .array(z.string())
              .optional()
              .describe("Exact warning, error, exception, or traceback text."),
          })
          .optional()
          .describe("Optional structured locate hints. Hints add ranking weight but are not hard filters."),
        failing_tests: z
          .array(z.string())
          .optional()
          .describe("Optional failing test names or node ids when available."),
      },
      async execute(args, context) {
        if (!projectDirectory) return "Error: codelocate requires a projectDirectory"
        try {
          const response = await makeRequest(config, {
            ...codeToolRequestOptions("locate", {
              source: {
                type: "local",
                path: projectDirectory,
              },
              query: args.query,
              terms: args.terms,
              hints: args.hints,
              failingTests: args.failing_tests,
              actorPeerId,
              abortSignal: context.abort,
            }),
          })
          return formatCodeLocateOutput(unwrapResponse(response), {
            projectDirectory,
          })
        } catch (error) {
          log("ERROR", "codelocate", "Locate failed", { error: error?.message, args })
          return `Error: ${error.message}`
        }
      },
    }),

    codesearch: tool({
      description:
        "Search code by ranked path, symbol, and content matches across a confirmed viking:// code repository or source subtree. " +
        "Use only after you have evidence that the uri contains supported source files. " +
        "Use when you do not know which file contains an implementation concept, option, error string, symbol, or test. " +
        "Do not use for general memory search, documentation-only resources, plain text notes, chat/session history, or local filesystem paths. " +
        "Returns compact ranked results with viking URI, local edit path, symbol context, and content snippets. " +
        "When a result includes `local:`, use that path directly for read/edit/test; use codeoutline/codeexpand only when you need more symbol structure.",
      args: {
        query: z.string().describe("Concept, option, error string, symbol, or path term to search for."),
        uri: z
          .string()
          .describe(
            "Viking URI for a confirmed ingested code repository or source subtree. Do not pass a local path or an unverified viking:// directory.",
          ),
      },
      async execute(args, context) {
        const validationError = validateVikingUri(args.uri, "codesearch")
        if (validationError) return validationError
        try {
          const response = await makeRequest(config, {
            ...codeToolRequestOptions("search", {
              uri: args.uri,
              query: args.query,
              actorPeerId,
              abortSignal: context.abort,
            }),
          })
          return formatCodeSearchOutput(unwrapResponse(response), {
            uri: args.uri,
            projectDirectory,
          })
        } catch (error) {
          log("ERROR", "codesearch", "Search failed", { error: error?.message, args })
          return `Error: ${error.message}`
        }
      },
    }),

    codeoutline: tool({
      description:
        "Show the symbol structure of a confirmed viking:// source file: classes, functions, methods, and line spans. " +
        "Use only for source files inside an ingested code repository, after you know the exact viking:// file URI. " +
        "Do not use on directories, documentation-only files, plain text notes, or files that are not supported source code. " +
        "Use read instead when you need the full file content.",
      args: {
        uri: z
          .string()
          .describe(
            "Viking URI of a confirmed supported source file, e.g. viking://resources/myproject/src/main.py.",
          ),
      },
      async execute(args, context) {
        const validationError = validateVikingUri(args.uri, "codeoutline")
        if (validationError) return validationError
        try {
          const response = await makeRequest(config, {
            ...codeToolRequestOptions("outline", {
              uri: args.uri,
              actorPeerId,
              abortSignal: context.abort,
            }),
          })
          return unwrapResponse(response)
        } catch (error) {
          log("ERROR", "codeoutline", "Outline failed", { error: error?.message, uri: args.uri })
          return `Error: ${error.message}`
        }
      },
    }),

    codeexpand: tool({
      description:
        "Return the full source of one named symbol from a confirmed viking:// source file. " +
        "Use only after codeoutline or other evidence shows the symbol exists in that file. " +
        "Do not use for broad exploration, non-code files, documentation, chat/session history, or unverified viking:// resources. " +
        "Accepts 'bar' (top-level function or class) or 'Foo.bar' (method inside class Foo). " +
        "For multiple symbols from the same file, read may be more efficient.",
      args: {
        uri: z
          .string()
          .describe("Viking URI of the confirmed supported source file containing the symbol."),
        symbol: z
          .string()
          .describe(
            "Symbol name: 'foo' for top-level, 'Foo.bar' for a method inside class Foo.",
          ),
      },
      async execute(args, context) {
        const validationError = validateVikingUri(args.uri, "codeexpand")
        if (validationError) return validationError
        try {
          const response = await makeRequest(config, {
            ...codeToolRequestOptions("expand", {
              uri: args.uri,
              symbol: args.symbol,
              actorPeerId,
              abortSignal: context.abort,
            }),
          })
          return unwrapResponse(response)
        } catch (error) {
          log("ERROR", "codeexpand", "Expand failed", { error: error?.message, args })
          return `Error: ${error.message}`
        }
      },
    }),
  }
  if (config?.codeTools?.locate === false) delete tools.codelocate
  if (config?.codeTools?.search === false) delete tools.codesearch
  if (config?.codeTools?.outline === false) delete tools.codeoutline
  if (config?.codeTools?.expand === false) delete tools.codeexpand
  return tools
}
