import { getConsoleAudit, getOvResult } from '#/shared/api/ov-client/index'
import type {
  ConsoleAuditQuery,
  ConsoleAuditResult,
} from '@ov-server/api/v1/console'

import type { AuditFilters } from '#/features/workbench/request-logs/types/audit'

export function buildAuditQuery(
  filters: AuditFilters,
  page: number,
  pageSize: number,
): ConsoleAuditQuery {
  const query: ConsoleAuditQuery = {
    page,
    page_size: pageSize,
  }

  const requestId = filters.requestId.trim()
  const statusCode = filters.statusCode.trim()
  const apiType = filters.apiType.trim()

  if (requestId) {
    query.request_id = requestId
  }

  if (apiType) {
    query.api_type = [apiType]
  }

  if (statusCode) {
    query.status = [statusCode]
  } else if (filters.logType === 'error') {
    query.status = ['error']
  }

  return query
}

export function isZeroResultCombination(filters: AuditFilters): boolean {
  if (filters.logType !== 'error') return false
  const rawStatusCode = filters.statusCode.trim()
  if (!rawStatusCode) return false
  const statusCode = Number(rawStatusCode)
  return Number.isFinite(statusCode) && statusCode < 400
}

export function fetchAuditLogs(
  filters: AuditFilters,
  page: number,
  pageSize: number,
): Promise<ConsoleAuditResult> {
  return getOvResult<ConsoleAuditResult>(
    getConsoleAudit({
      query: buildAuditQuery(filters, page, pageSize),
    }),
  )
}
