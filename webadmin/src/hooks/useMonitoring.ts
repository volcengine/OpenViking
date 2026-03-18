import { useQuery } from '@tanstack/react-query'
import { monitoringService, MonitoringSummary } from '../services/monitoring'

// Query key for monitoring
export const MONITORING_QUERY_KEY = ['monitoring']

// Hook options interface
export interface UseMonitoringOptions {
  enabled?: boolean
  refetchInterval?: number
}

// Monitoring hook - gets all data from single /observer/system endpoint
export const useMonitoring = (options: UseMonitoringOptions = {}) => {
  const { enabled = true, refetchInterval = 30000 } = options

  return useQuery<MonitoringSummary, Error>({
    queryKey: MONITORING_QUERY_KEY,
    queryFn: async () => {
      const response = await monitoringService.getAll()
      if (!response.success || !response.data) {
        throw new Error('Failed to fetch monitoring data')
      }
      return response.data
    },
    refetchInterval: enabled ? refetchInterval : false,
    refetchOnWindowFocus: true,
    staleTime: 10000 // 10 seconds
  })
}

// Individual monitoring hooks - all use getAll which fetches from single endpoint
export const useSystemStatus = () => {
  return useQuery({
    queryKey: MONITORING_QUERY_KEY,
    queryFn: async () => {
      const response = await monitoringService.getAll()
      return response.data?.system
    },
    refetchInterval: 30000,
    select: (data) => data
  })
}

export default useMonitoring
