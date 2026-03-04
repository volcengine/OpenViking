import { exec } from "child_process"
import { promisify } from "util"

const execAsync = promisify(exec)

let cachedRepos: string | null = null

async function loadRepos(): Promise<void> {
  try {
    const { stdout } = await execAsync(
      "ov --output json ls viking://resources/ --abs-limit 2000",
      { timeout: 8000 }
    )
    const parsed = JSON.parse(stdout)
    const items: Array<{ uri: string; abstract?: string }> = parsed?.result ?? []
    const repos = items
      .filter((item) => item.uri?.startsWith("viking://resources/"))
      .map((item) => {
        const name = item.uri.replace("viking://resources/", "").replace(/\/$/, "")
        return item.abstract
          ? `- **${name}** (${item.uri})\n  ${item.abstract}`
          : `- **${name}** (${item.uri})`
      })
    if (repos.length > 0) {
      cachedRepos = repos.join("\n")
    }
  } catch {
    // openviking not available — plugin is a no-op
  }
}

/**
 * @type {import('@opencode-ai/plugin').Plugin}
 */
export async function OpenVikingContextPlugin() {
  // Fetch repos at startup so the cache is ready before any request
  await loadRepos()

  return {
    // Inject repo list into every LLM request's system prompt (sync — no await)
    "experimental.chat.system.transform": (
      _input: unknown,
      output: { system: string[] }
    ) => {
      if (!cachedRepos) return
      output.system.push(
        `## OpenViking — Indexed Code Repositories\n\n` +
        `The following repos are semantically indexed and searchable.\n` +
        `When the user asks about any of these projects or their internals, ` +
        `you MUST proactively call skill("openviking") before answering.\n\n` +
        cachedRepos
      )
    },

    // Refresh repo list when a new session starts
    "session.created": async () => {
      await loadRepos()
    },
  }
}
