import type * as React from 'react'
import { ActivityIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Card, CardContent, CardHeader, CardTitle } from '#/components/ui/card'
import {
  Table,
  TableBody,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import type { ConsoleAuditLogItem } from '@ov-server/api/v1/console'

import { EmptyLogsState } from './empty-logs-state'
import { RequestLogFilters } from './filters'
import { RequestLogPagination } from './pagination'
import { RequestLogRow } from './row'
import type { AuditFilters, LogTypeFilter } from '../-types/audit'

type RequestLogPanelProps = {
  disabled: boolean
  disabledMessage?: string
  draftFilters: AuditFilters
  filters: AuditFilters
  isError: boolean
  isFetching: boolean
  isLoading: boolean
  logs: ConsoleAuditLogItem[]
  onDraftFiltersChange: React.Dispatch<React.SetStateAction<AuditFilters>>
  onLogTypeChange: (logType: LogTypeFilter) => void
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  onRefresh: () => void
  onReset: () => void
  onSearch: () => void
  page: number
  pageCount: number
  pageSize: number
  total: number
  zeroResult: boolean
}

export function RequestLogPanel({
  disabled,
  disabledMessage,
  draftFilters,
  filters,
  isError,
  isFetching,
  isLoading,
  logs,
  onDraftFiltersChange,
  onLogTypeChange,
  onPageChange,
  onPageSizeChange,
  onRefresh,
  onReset,
  onSearch,
  page,
  pageCount,
  pageSize,
  total,
  zeroResult,
}: RequestLogPanelProps) {
  const { t } = useTranslation('requestLogs')

  return (
    <Card className="overflow-hidden">
      <CardHeader className="gap-4 border-b bg-muted/20">
        <div className="grid min-w-0 grid-cols-[9rem_minmax(0,1fr)] items-center gap-3">
          <CardTitle className="text-base leading-tight whitespace-nowrap">
            {t('table.title')}
          </CardTitle>
          <RequestLogFilters
            draftFilters={draftFilters}
            filters={filters}
            isFetching={isFetching}
            onDraftFiltersChange={onDraftFiltersChange}
            onLogTypeChange={onLogTypeChange}
            onRefresh={onRefresh}
            onReset={onReset}
            onSearch={onSearch}
            zeroResult={zeroResult}
          />
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading && !zeroResult ? (
          <div className="flex min-h-72 items-center justify-center text-sm text-muted-foreground">
            {t('loading')}
          </div>
        ) : isError ? (
          <EmptyLogsState
            title={t('error.title')}
            description={t('error.description')}
          />
        ) : disabled ? (
          <EmptyLogsState
            title={t('disabled.title')}
            description={disabledMessage || t('disabled.description')}
          />
        ) : logs.length === 0 ? (
          <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
            <div className="flex size-11 items-center justify-center rounded-lg border bg-muted/30 text-muted-foreground">
              <ActivityIcon className="size-5" />
            </div>
            <div>
              <p className="font-medium">{t('empty.title')}</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {t('empty.description')}
              </p>
            </div>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/20 hover:bg-muted/20">
                    <TableHead>{t('table.time')}</TableHead>
                    <TableHead>{t('table.apiType')}</TableHead>
                    <TableHead>{t('table.method')}</TableHead>
                    <TableHead>{t('table.path')}</TableHead>
                    <TableHead>{t('table.status')}</TableHead>
                    <TableHead className="text-right">
                      {t('table.duration')}
                    </TableHead>
                    <TableHead>{t('table.requestId')}</TableHead>
                    <TableHead>{t('table.accountId')}</TableHead>
                    <TableHead>{t('table.userId')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {logs.map((log, index) => (
                    <RequestLogRow
                      key={`${log.request_id ?? 'request'}-${log.created_at ?? index}`}
                      log={log}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
            <RequestLogPagination
              page={page}
              pageCount={pageCount}
              pageSize={pageSize}
              total={total}
              onPageChange={onPageChange}
              onPageSizeChange={onPageSizeChange}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}
