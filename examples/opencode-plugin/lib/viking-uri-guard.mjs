import { log } from "./utils.mjs"
import { evaluateUriGuard, findVikingUri, normalizeToolName } from "./shared/uri-guard.mjs"

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
    const args = output?.args ?? input?.args ?? {}
    const decision = evaluateUriGuard(toolName, args, { hints: FILESYSTEM_TOOL_HINTS })
    if (!decision) return

    log("INFO", "viking-uri-guard", "Blocked filesystem tool for viking URI", {
      tool: toolName,
      uri: decision.uri,
    })
    throw new Error(decision.reason)
  }
}

export { findVikingUri, normalizeToolName }
