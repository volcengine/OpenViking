import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join, resolve as resolvePath } from "node:path"

function readJson(path: string): Record<string, unknown> {
  return JSON.parse(readFileSync(path, "utf-8")) as Record<string, unknown>
}

export function loadOvConf(env: NodeJS.ProcessEnv = process.env): Record<string, unknown> {
  const defaultPath = join(homedir(), ".openviking", "ov.conf")
  const configPath = resolvePath(
    (env.OPENVIKING_CONFIG_FILE || defaultPath).replace(/^~/, homedir()),
  )
  try {
    return readJson(configPath)
  } catch (err) {
    const code = (err as { code?: string })?.code
    const detail = code === "ENOENT" ? `Config file not found: ${configPath}` : `Invalid config file: ${configPath}`
    process.stderr.write(`[openviking-memory] ${detail}\n`)
    process.exit(1)
  }
}

export function loadClientConfig(env: NodeJS.ProcessEnv = process.env): Record<string, unknown> {
  const defaultPath = join(homedir(), ".openviking", "codex-memory-plugin", "config.json")
  const configPath = resolvePath(
    (env.OPENVIKING_CODEX_CONFIG_FILE || defaultPath).replace(/^~/, homedir()),
  )
  try {
    return readJson(configPath)
  } catch (err) {
    const code = (err as { code?: string })?.code
    if (code === "ENOENT") return {}
    process.stderr.write(`[openviking-memory] Invalid client config: ${configPath}\n`)
    process.exit(1)
  }
}

export type ResolvedConfig = {
  baseUrl: string
  apiKey: string
  accountId: string
  userId: string
  agentId: string
  timeoutMs: number
  recallLimit: number
  scoreThreshold: number
}

function str(value: unknown, fallback: string): string {
  if (typeof value === "string" && value.trim()) return value.trim()
  return fallback
}

function num(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return fallback
}

export function resolveConfig(
  ovConf: Record<string, unknown>,
  clientConfig: Record<string, unknown>,
  env: NodeJS.ProcessEnv,
): ResolvedConfig {
  const serverConfig = (ovConf.server ?? {}) as Record<string, unknown>
  const host = str(serverConfig.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1")
  const port = Math.floor(num(serverConfig.port, 1933))

  return {
    baseUrl: `http://${host}:${port}`,
    apiKey: str(env.OPENVIKING_API_KEY, str(clientConfig.apiKey, str(serverConfig.root_api_key, ""))),
    accountId: str(env.OPENVIKING_ACCOUNT, str(clientConfig.accountId, str(ovConf.default_account, "default"))),
    userId: str(env.OPENVIKING_USER, str(clientConfig.userId, str(ovConf.default_user, "default"))),
    agentId: str(env.OPENVIKING_AGENT_ID, str(clientConfig.agentId, str(ovConf.default_agent, "codex"))),
    timeoutMs: Math.max(1000, Math.floor(num(env.OPENVIKING_TIMEOUT_MS, num(clientConfig.timeoutMs, 15000)))),
    recallLimit: Math.max(1, Math.floor(num(env.OPENVIKING_RECALL_LIMIT, num(clientConfig.recallLimit, 6)))),
    scoreThreshold: Math.min(1, Math.max(0, num(env.OPENVIKING_SCORE_THRESHOLD, num(clientConfig.scoreThreshold, 0.01)))),
  }
}
