import type { PerformanceQuery, PerformanceResult } from '@ov-server/api/v1/console'
import { getOvClient, getOvResult, isOvClientError } from '#/shared/api/ov-client/index'

export async function fetchPerformance<T>(query: PerformanceQuery, signal?: AbortSignal): Promise<PerformanceResult<T>> {
  try {
    return await getOvResult<PerformanceResult<T>>(
      getOvClient().instance.get('/api/v1/console/performance', { params: query, signal }),
    )
  } catch (error) {
    if (isOvClientError(error) && error.statusCode === 404) {
      return { enabled: false, unsupported: true }
    }
    throw error
  }
}
