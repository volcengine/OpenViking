import type { AuditFilters, LogTypeFilter } from '../-types/audit'

export const DEFAULT_FILTERS: AuditFilters = {
  apiType: '',
  logType: 'all',
  requestId: '',
  statusCode: '',
}

export const LOG_TYPE_FILTERS: LogTypeFilter[] = ['all', 'error']

export const DEFAULT_PAGE_SIZE = 10

export const PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const
