import { createOvClient, getOvResult, OvClientError } from '#/lib/ov-client'
import type { ConnectionDraft } from '#/hooks/use-app-connection'

export const modelKinds = ['vlm', 'embedding'] as const
export type ModelKind = (typeof modelKinds)[number]
export type ModelConfig = Record<string, unknown>
export type ModelChanges = Partial<Record<ModelKind, ModelConfig>>
export type ModelEntry = {
  config: ModelConfig
}
export type ConfigFileDraft = {
  content: string
  form_readonly?: boolean
  models: Record<ModelKind, ModelEntry>
}
export type ConfigFileConfiguration = ConfigFileDraft & {
  revision: string
  file_path?: string
  writable?: boolean
  restart_required?: boolean
  restart?: RestartStatus
  overrides?: { cluster: string[]; account: string[] }
}
export type RestartStatus = {
  supported: boolean
  instance_id: string
  restarting: boolean
  rolled_back?: boolean
}
export async function waitForServerRestart(
  load: () => Promise<ConfigFileConfiguration>,
  instanceId: string,
  signal: AbortSignal,
  timeoutMs = 120_000,
) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    signal.throwIfAborted()
    try {
      const result = await load()
      signal.throwIfAborted()
      if (
        result.restart &&
        result.restart.instance_id !== instanceId &&
        !result.restart.restarting
      )
        return result
    } catch (error) {
      signal.throwIfAborted()
      if (
        error instanceof OvClientError &&
        (error.statusCode === 401 || error.statusCode === 403)
      )
        throw error
      // Temporary connection errors are expected during graceful shutdown.
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error('Restart timed out')
}
export const embeddingModes = ['dense', 'sparse', 'hybrid'] as const
export const embeddingCredentialFields = [
  'id',
  'provider',
  'model',
  'api_key',
  'api_base',
  'api_version',
  'ak',
  'sk',
  'region',
  'host',
  'extra_headers',
]

export function isConfigFileObject(content: string): boolean {
  try {
    // Startup expands unquoted environment values before parsing JSON.
    // Only check their syntax here; the server owns expansion and validation.
    const parsed: unknown = JSON.parse(
      content
        .trimStart()
        .replace(/"(?:[^"\\]|\\.)*"|\$(?:\{[^}]*\}|[a-zA-Z0-9_]+)/g, (token) =>
          token.startsWith('"') ? token : JSON.stringify(token),
        ),
    )
    return (
      parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
    )
  } catch {
    return false
  }
}

export function object(value: unknown): ModelConfig {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as ModelConfig)
    : {}
}
export function credentials(config: ModelConfig): ModelConfig[] {
  return Array.isArray(config.credentials) ? config.credentials.map(object) : []
}
export function embeddingPatch(config: ModelConfig): ModelConfig {
  const patch: ModelConfig = {}
  for (const key of ['max_retries', 'max_concurrent', 'circuit_breaker']) {
    if (config[key] !== undefined) patch[key] = config[key]
  }
  for (const mode of embeddingModes) {
    if (!config[mode]) continue
    const section = object(config[mode])
    patch[mode] = {
      credentials: credentials(section).map((credential) =>
        Object.fromEntries(
          Object.entries(credential).filter(([key]) =>
            embeddingCredentialFields.includes(key),
          ),
        ),
      ),
      ...Object.fromEntries(
        ['failback_timeout_seconds', 'failback_request_count']
          .filter((key) => section[key] !== undefined)
          .map((key) => [key, section[key]]),
      ),
    }
  }
  return patch
}

export function createConfigFileApi(
  connection: ConnectionDraft,
  trusted: boolean,
) {
  const { client } = createOvClient({
    baseUrl: connection.baseUrl,
    connection: { ...connection, identityHeaders: trusted },
  })
  const url = '/api/v1/admin/configuration'
  return {
    restart: (revision: string) =>
      getOvResult<RestartStatus>(
        client.post({
          url: '/api/v1/admin/restart',
          headers: { 'Content-Type': 'application/json' },
          body: { revision },
        }),
      ),
    get: async (timeout = 0) => {
      const result = await getOvResult<
        Omit<ConfigFileConfiguration, 'models'> & {
          models?: Partial<ConfigFileConfiguration['models']>
        }
      >(
        client.get({
          url,
          timeout,
          query: { source: 'file', account_id: connection.accountId },
        }),
      )
      const models = result.models
      if (
        typeof result.content !== 'string' ||
        !models ||
        modelKinds.some((kind) => !models[kind])
      )
        throw new Error('Model configuration response is unavailable')
      return { ...result, models: models as ConfigFileConfiguration['models'] }
    },
    preview: (content: string, changes: ModelChanges = {}) =>
      getOvResult<ConfigFileDraft>(
        client.patch({
          url,
          query: { source: 'file', dry_run: true },
          headers: { 'Content-Type': 'application/json' },
          body: {
            content,
            settings: Object.fromEntries(
              Object.entries(changes).map(([kind, config]) => [
                kind,
                kind === 'embedding' ? embeddingPatch(config) : config,
              ]),
            ),
          },
        }),
      ),
    save: (content: string, revision: string) =>
      getOvResult<{ revision: string; restart_required: boolean }>(
        client.patch({
          url,
          query: { source: 'file' },
          headers: { 'Content-Type': 'application/json' },
          body: { revision, content },
        }),
      ),
  }
}
