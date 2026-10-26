import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { probeConnection } from '#/lib/connection/probe'
import { useAppConnection } from './use-app-connection'

export function useConnectionProbe() {
  const { connection, serverMode } = useAppConnection()
  return useQuery({
    enabled: Boolean(connection.baseUrl) && serverMode !== 'checking',
    placeholderData: keepPreviousData,
    queryFn: () => probeConnection(connection, serverMode),
    queryKey: [
      'studio-connection-probe',
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
