import { effectivePeerId, log, makeRequest, unwrapResponse } from "./utils.mjs"

export function createRepoContext({ config }) {
  let cachedRepos = null
  let lastFetchTime = 0

  async function refreshRepos({ force = false } = {}) {
    if (!config.repoContext?.enabled) return null

    const now = Date.now()
    const ttl = config.repoContext?.cacheTtlMs ?? 60000
    if (!force && cachedRepos !== null && now - lastFetchTime < ttl) {
      return cachedRepos
    }

    try {
      const response = await makeRequest(config, {
        method: "GET",
        endpoint: `/api/v1/fs/ls?uri=${encodeURIComponent("viking://resources/")}&recursive=false&simple=false`,
        timeoutMs: 8000,
        actorPeerId: effectivePeerId(config),
      })
      const result = unwrapResponse(response)
      const items = Array.isArray(result) ? result : []
      const repos = items
        .filter((item) => item?.uri?.startsWith("viking://resources/") && item.uri !== "viking://resources/")
        .map(formatRepoLine)

      cachedRepos = repos.length > 0 ? repos.join("\n") : ""
      lastFetchTime = now
      log("INFO", "repo-context", "Repo context refreshed", { count: repos.length })
      return cachedRepos
    } catch (error) {
      log("WARN", "repo-context", "Failed to refresh indexed repositories", { error: error?.message })
      return cachedRepos
    }
  }

  function getRepoSystemPrompt() {
    if (!config.repoContext?.enabled || !cachedRepos) return null
    return [
      "## OpenViking - Indexed Code Repositories",
      "",
      "The following external repositories are indexed in OpenViking and searchable through tools.",
      "When the user asks about these projects or their internals, use the OpenViking tools before answering.",
      "",
      "Tool guidance:",
      "- Use `memsearch` for semantic or conceptual repository questions.",
      "- Use `memgrep` for exact symbols, error strings, class names, function names, and regex-like searches.",
      "- Use `memglob` to enumerate files by pattern.",
      "- Use `membrowse` to inspect directory structure and `memread` to read specific URIs.",
      "- Use `memadd`, `memremove`, and `memqueue` for repository resource management when explicitly requested.",
      "",
      cachedRepos,
    ].join("\n")
  }

  return {
    refreshRepos,
    getRepoSystemPrompt,
  }
}

/**
 * Merge the repo-context system prompt into OpenCode's system array without
 * adding a new system block.
 *
 * OpenCode serializes every entry of `output.system` into its own
 * `{ role: "system" }` message and only re-collapses the array when it holds
 * more than two entries (session/llm/request.ts). Pushing a new entry onto the
 * single collapsed system prompt therefore produces two leading system
 * messages, which providers that require a single system message (e.g. litellm
 * proxying to OpenAI) reject with "System message must be at the beginning".
 * Appending to the last existing entry keeps the block count unchanged, so the
 * conversation still starts with exactly one system message. See issue #2885.
 *
 * @param {{ system?: string[] }} output OpenCode system-transform hook output.
 * @param {string | null | undefined} prompt Repo-context prompt to inject.
 * @returns {boolean} Whether the prompt was injected.
 */
export function applyRepoSystemPrompt(output, prompt) {
  if (!prompt) return false
  const system = output?.system
  if (!Array.isArray(system)) return false
  if (system.length > 0) {
    const last = system[system.length - 1]
    system[system.length - 1] = last ? `${last}\n\n${prompt}` : prompt
  } else {
    system.push(prompt)
  }
  return true
}

function formatRepoLine(item) {
  const name = item.uri.replace("viking://resources/", "").replace(/\/$/, "") || "resources"
  const abstract = item.abstract || item.overview
  return abstract ? `- **${name}** (${item.uri})\n  ${abstract}` : `- **${name}** (${item.uri})`
}
