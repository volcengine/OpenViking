import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { fetchStudioCapability } from '#/features/connection/lib/capabilities'
import { STUDIO_FEATURES } from '#/shared/lib/studio-service'
import { useQueries } from '@tanstack/react-query'

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
