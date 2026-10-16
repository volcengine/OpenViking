import { resolveApiKeyAuth } from './auth'
import { normalizeOvClientError, createOvClient } from '#/lib/ov-client'
import {
  classifyCapabilityError,
  classifyCapabilityResponse,
} from '../studio-service'
import type { CapabilityState, STUDIO_FEATURES } from '../studio-service'
import type { ConnectionDraft } from './types'

type ProbedFeature = Extract<
  (typeof STUDIO_FEATURES)[number],
  { probe: string }
>

/** Read capability using the caller's immutable connection snapshot. */
export async function fetchStudioCapability(
  connection: ConnectionDraft,
  feature: ProbedFeature,
  signal: AbortSignal,
): Promise<CapabilityState> {
  const auth = resolveApiKeyAuth(
    connection.baseUrl,
    connection.serviceSelection,
  )
  try {
    // Capture credentials with the query's scope. A mutable global client
    // can otherwise attach a new key to an old connection's request.
    const client = createOvClient({
      apiKeyStorageKey: '',
      apiKeyAuth: auth,
      baseUrl: connection.baseUrl,
      connection: {
        apiKey:
          auth === 'bearer'
            ? connection.apiKey
            : connection.apiKey || connection.adminApiKey,
      },
    })
    const response = await client.instance.get(feature.probe, {
      baseURL: connection.baseUrl.replace(/\/+$/, ''),
      signal,
      timeout: 8_000,
    })
    return classifyCapabilityResponse(feature.id, response.data)
  } catch (error) {
    return classifyCapabilityError(normalizeOvClientError(error))
  }
}
