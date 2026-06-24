import { tool } from "@opencode-ai/plugin"
import { effectivePeerId, log, makeRequest, unwrapResponse, validateVikingUri } from "./utils.mjs"

const z = tool.schema

const CODE_TOOL_ENDPOINTS = {
  search: "/api/v1/code/search",
  outline: "/api/v1/code/outline",
  expand: "/api/v1/code/expand",
}

function codeToolRequestOptions(kind, { uri, query, symbol, actorPeerId, abortSignal }) {
  const body = { uri }
  if (kind === "search") body.query = query
  if (kind === "expand") body.symbol = symbol

  return {
    method: "POST",
    endpoint: CODE_TOOL_ENDPOINTS[kind],
    body,
    abortSignal,
    actorPeerId,
  }
}

export function createCodeTools({ config }) {
  const actorPeerId = effectivePeerId(config)
  return {
    codesearch: tool({
      description:
        "Search AST-supported symbol names (classes, functions, methods) by substring across a confirmed viking:// code repository or source subtree. " +
        "Use only after you have evidence that the uri contains supported source files. " +
        "Use when you do not know which file contains a symbol. " +
        "Do not use for general memory search, documentation-only resources, plain text notes, chat/session history, or local filesystem paths. " +
        "Returns structured results: symbol name, class context, file URI, line range. " +
        "Typical workflow: verify code repo with ls/glob/add_resource output, then codesearch, codeoutline, codeexpand.",
      args: {
        query: z.string().describe("Symbol name substring to search for (case-insensitive)."),
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
          return unwrapResponse(response)
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
}
