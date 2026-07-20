import { log } from "./utils.mjs"
import { buildGuardMessage, findVikingUri, normalizeToolName } from "./shared/uri-guard.mjs"

const FILESYSTEM_TOOL_HINTS = {
  read: {
    tool: "openviking_read",
    example: (uri) => `openviking_read(uris=["${uri}"])`,
  },
  glob: {
    tool: "openviking_glob",
    example: (uri) => `openviking_glob(uri="${uri}", pattern="**/*")`,
  },
  grep: {
    tool: "openviking_search",
    example: (uri, args = {}) => `openviking_search(query="${String(args.pattern ?? "").replaceAll('"', '\\"')}", target_uri="${uri}")`,
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
    throw new Error(buildGuardMessage(uri, { ...hint, example: hint.example(uri, args) }))
  }
}

export { findVikingUri, normalizeToolName }
