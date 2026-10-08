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
export type ModelConfiguration = {
  revision?: string
  file_path?: string
  writable?: boolean
  restart_required?: boolean
  overrides?: { cluster: string[]; account: string[] }
  settings: Partial<Record<ModelKind, ModelConfig>>
  models: Record<ModelKind, ModelEntry>
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

export function createModelManagementApi(
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
        Omit<ModelConfiguration, 'models'> & {
          models?: Partial<ModelConfiguration['models']>
        }
      >(
        client.get({
          url,
          query: { source: 'file', account_id: connection.accountId },
        }),
      )
      const models = result.models
      if (!models || modelKinds.some((kind) => !models[kind]))
        throw new Error('Model configuration response is unavailable')
      return { ...result, models: models as ModelConfiguration['models'] }
    },
    save: (changes: ModelChanges, revision?: string) =>
      getOvResult(
        client.patch({
          url,
          query: { source: 'file' },
          headers: { 'Content-Type': 'application/json' },
          body: {
            revision,
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
  }
}
