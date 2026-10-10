import { createOvClient, getOvResult } from '#/lib/ov-client'
import type { ConnectionDraft } from '#/hooks/use-app-connection'

export const modelKinds = [
  'vlm',
  'embedding',
  'query_planner',
  'rerank',
] as const
export type ModelKind = (typeof modelKinds)[number]
export type ModelConfig = Record<string, unknown>
export type ModelChanges = Partial<Record<ModelKind, ModelConfig | null>>
export type ModelEntry = {
  source: 'account' | 'server' | 'vlm'
  config: ModelConfig
  available?: boolean
}
export type ConfigFileDraft = {
  content: string
  models: Record<ModelKind, ModelEntry>
}
export type ConfigFileConfiguration = ConfigFileDraft & {
  revision: string
  file_path?: string
  writable?: boolean
  restart_required?: boolean
  overrides?: { cluster: string[]; account: string[] }
  settings: Partial<Record<ModelKind, ModelConfig>>
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
      content.replace(
        /"(?:[^"\\]|\\.)*"|\$(?:\{[^}]*\}|[a-zA-Z0-9_]+)/g,
        (token) => (token.startsWith('"') ? token : JSON.stringify(token)),
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
    get: async () => {
      const result = await getOvResult<
        Omit<ConfigFileConfiguration, 'models'> & {
          models?: Partial<ConfigFileConfiguration['models']>
        }
      >(
        client.get({
          url,
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
                kind === 'embedding' && config
                  ? embeddingPatch(config)
                  : config,
              ]),
            ),
          },
        }),
      ),
    save: (content: string, revision: string) =>
      getOvResult(
        client.patch({
          url,
          query: { source: 'file' },
          headers: { 'Content-Type': 'application/json' },
          body: { revision, content },
        }),
      ),
  }
}
