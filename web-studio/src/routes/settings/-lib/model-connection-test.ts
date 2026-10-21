import { createConfigFileApi } from './config-file-api'
import type { ModelConfig, ModelKind } from './config-file-api'
import type { ConnectionDraft } from '#/hooks/use-app-connection'

export class ModelTestError extends Error {
  constructor(public readonly code: string) {
    super(code)
  }
}
export function supportsModelTest(kind: ModelKind, provider: unknown) {
  return (
    kind === 'vlm'
      ? ['openai', 'volcengine', 'azure', 'glm', 'kimi', 'litellm']
      : [
          'openai',
          'azure',
          'volcengine',
          'jina',
          'gemini',
          'voyage',
          'dashscope',
          'minimax',
          'cohere',
          'litellm',
        ]
  ).includes(String(provider || '').toLowerCase())
}

export async function testModelConnection({
  connection,
  trusted,
  signal,
  ...draft
}: {
  connection: ConnectionDraft
  trusted: boolean
  kind: ModelKind
  config: ModelConfig
  fallback: ModelConfig
  thinking?: boolean
  mode?: string
  signal: AbortSignal
}) {
  const result = await createConfigFileApi(connection, trusted).testModel(
    draft,
    signal,
  )
  if (!result.ok) throw new ModelTestError(result.code || 'failed')
  return result.ms ?? 0
}
