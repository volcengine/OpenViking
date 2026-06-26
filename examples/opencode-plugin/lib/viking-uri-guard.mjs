import { log } from "./utils.mjs"

const FILESYSTEM_TOOL_HINTS = {
  read: {
    replacement: "memread",
    example: (uri) => `memread(uri="${uri}", level="auto")`,
  },
  glob: {
    replacement: "membrowse",
    example: (uri) => `membrowse(uri="${uri}", view="list", recursive=true)`,
  },
  grep: {
    replacement: "memsearch",
    example: (uri, args) => `memsearch(query="${String(args.pattern ?? "").replaceAll('"', '\\"')}", target_uri="${uri}")`,
  },
}

export function createVikingUriGuard() {
  return async (input, output) => {
    const toolName = normalizeToolName(input?.tool ?? input?.name)
    const hint = FILESYSTEM_TOOL_HINTS[toolName]
    if (!hint) return

    const args = output?.args ?? input?.args ?? {}
    const uri = findVikingUri(args)
    if (!uri) return

    log("INFO", "viking-uri-guard", "Blocked filesystem tool for viking URI", {
      tool: toolName,
      uri,
    })
    throw new Error(
      [
        "viking:// URIs are OpenViking virtual paths, not local filesystem paths.",
        `Use ${hint.replacement} instead.`,
        `Example: ${hint.example(uri, args)}`,
      ].join("\n"),
    )
  }
}

export function findVikingUri(args = {}) {
  for (const key of ["filePath", "path", "uri"]) {
    const value = args[key]
    if (typeof value === "string" && value.startsWith("viking://")) return value
  }
  return null
}

function normalizeToolName(value) {
  return String(value || "").trim().toLowerCase()
}
