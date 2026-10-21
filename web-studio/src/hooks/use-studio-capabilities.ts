import { useQueries } from '@tanstack/react-query'
import { useAppConnection } from './use-app-connection'
import { useStudioService } from './use-studio-service'
import { STUDIO_FEATURES } from '#/lib/studio-service'
import { fetchStudioCapability } from '#/lib/connection/capabilities'

// Only inexpensive, object-free GETs. Do not probe writes, imports, or search.
export function useStudioCapabilities() {
  const { connection, identityScopeKey } = useAppConnection()
  const service = useStudioService()
  const features = STUDIO_FEATURES.filter((f) => 'probe' in f)
  const results = useQueries({
    queries: features.map((feature) => ({
      queryKey: ['studio-feature', identityScopeKey, feature.id],
      enabled: service.ready && service.provider === 'volcengine',
      queryFn: async ({ signal }: { signal: AbortSignal }) => {
        return fetchStudioCapability(connection, feature, signal)
      },
      retry: false,
      staleTime: 60_000,
    })),
  })
  const capabilities: Partial<
    Record<(typeof STUDIO_FEATURES)[number]['id'], (typeof results)[number]>
  > = Object.fromEntries(
    features.map((feature, index) => [feature.id, results[index]]),
  )
  return capabilities
}
