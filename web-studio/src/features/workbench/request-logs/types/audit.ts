export type LogTypeFilter = 'all' | 'error'

export type RequestLogStatus = 'success' | 'error'

export type AuditFilters = {
  apiType: string
  logType: LogTypeFilter
  requestId: string
  statusCode: string
}
