import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { probeConnection } from '#/features/connection/lib/probe'
import { keepPreviousData, useQuery } from '@tanstack/react-query'

export function useConnectionProbe() {
  const { connection, serverMode, studioMode } = useAppConnection()
  return useQuery({
    enabled: Boolean(connection.baseUrl) && serverMode !== 'checking',
    placeholderData: keepPreviousData,
    queryFn: () =>
      probeConnection(
        studioMode === 'management' || serverMode === 'trusted'
          ? connection
          : { ...connection, adminApiKey: '' },
        serverMode,
        studioMode === 'management',
      ),
    queryKey: [
      'studio-connection-probe',
      studioMode,
      connection.serviceSelection,
      connection.baseUrl,
      connection.adminApiKey,
      connection.apiKey,
      connection.accountId,
      connection.userId,
      serverMode,
    ],
    retry: false,
    staleTime: 15_000,
  })
}
