import { log } from "./utils.mjs"
import { evaluateUriGuard, evaluateUriNotice, findVikingUri, normalizeToolName } from "./shared/uri-guard.mjs"

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
  bash: {
    tool: "openviking_read or openviking_search",
    example: (uri) => `openviking_read(uris=["${uri}"])`,
  },
  shell: {
    tool: "openviking_read or openviking_search",
    example: (uri) => `openviking_read(uris=["${uri}"])`,
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

export function createVikingUriNotice() {
  return async (input, output) => {
    const toolName = normalizeToolName(input?.tool ?? input?.name)
    const notice = evaluateUriNotice(toolName, input?.args ?? {}, { hints: FILESYSTEM_TOOL_HINTS })
    if (!notice || !output) return

    log("INFO", "viking-uri-guard", "Attached viking URI notice to shell tool output", {
      tool: toolName,
      uri: notice.uri,
    })
    output.output = output.output ? `${output.output}\n\n${notice.reason}` : notice.reason
  }
}

export { findVikingUri, normalizeToolName }
