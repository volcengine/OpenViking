import type { AuditFilters, LogTypeFilter } from '../-types/audit'

export const DEFAULT_FILTERS: AuditFilters = {
  apiType: '',
  logType: 'all',
  requestId: '',
  statusCode: '',
}

export const LOG_TYPE_FILTERS: LogTypeFilter[] = ['all', 'error']

export const PAGE_SIZE = 10
