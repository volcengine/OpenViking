import type { ConnectionDraft } from '#/features/connection/lib/types'
import { resolveApiKeyAuth } from '#/shared/api/auth'
import {
  createOvClient,
  normalizeOvClientError,
} from '#/shared/api/ov-client/index'
import type {
  CapabilityState,
  STUDIO_FEATURES,
} from '#/shared/lib/studio-service'
import {
  classifyCapabilityError,
  classifyCapabilityResponse,
} from '#/shared/lib/studio-service'

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
      credentialScope: 'data',
      apiKeyAuth: auth,
      baseUrl: connection.baseUrl,
      connection: {
        apiKey: connection.apiKey,
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
