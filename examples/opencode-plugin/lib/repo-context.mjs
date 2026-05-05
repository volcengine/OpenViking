import { log, makeRequest, unwrapResponse } from "./utils.mjs"

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

function formatRepoLine(item) {
  const name = item.uri.replace("viking://resources/", "").replace(/\/$/, "") || "resources"
  const abstract = item.abstract || item.overview
  return abstract ? `- **${name}** (${item.uri})\n  ${abstract}` : `- **${name}** (${item.uri})`
}

