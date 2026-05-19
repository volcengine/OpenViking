import type * as React from 'react'
import { RefreshCwIcon, RotateCcwIcon, SearchIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { cn } from '#/lib/utils'

import { LOG_TYPE_FILTERS } from '../-constants/audit'
import type { AuditFilters, LogTypeFilter } from '../-types/audit'

type RequestLogFiltersProps = {
  draftFilters: AuditFilters
  filters: AuditFilters
  isFetching: boolean
  onDraftFiltersChange: React.Dispatch<React.SetStateAction<AuditFilters>>
  onLogTypeChange: (logType: LogTypeFilter) => void
  onRefresh: () => void
  onReset: () => void
  onSearch: () => void
  zeroResult: boolean
}

export function RequestLogFilters({
  draftFilters,
  filters,
  isFetching,
  onDraftFiltersChange,
  onLogTypeChange,
  onRefresh,
  onReset,
  onSearch,
  zeroResult,
}: RequestLogFiltersProps) {
  const { t } = useTranslation('requestLogs')

  return (
    <form
      className="grid min-w-0 grid-cols-[minmax(12rem,1fr)_6.5rem_9rem_auto_auto_auto_auto] items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        onSearch()
      }}
    >
      <div className="relative min-w-0">
        <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={draftFilters.requestId}
          onChange={(event) =>
            onDraftFiltersChange((current) => ({
              ...current,
              requestId: event.target.value,
            }))
          }
          placeholder={t('filters.requestIdPlaceholder')}
          className="pl-8"
        />
      </div>
      <Input
        value={draftFilters.statusCode}
        onChange={(event) =>
          onDraftFiltersChange((current) => ({
            ...current,
            statusCode: event.target.value.replace(/\D/g, '').slice(0, 3),
          }))
        }
        placeholder={t('filters.statusCodePlaceholder')}
        className="w-full"
        inputMode="numeric"
      />
      <Input
        value={draftFilters.apiType}
        onChange={(event) =>
          onDraftFiltersChange((current) => ({
            ...current,
            apiType: event.target.value,
          }))
        }
        placeholder={t('filters.apiTypePlaceholder')}
        className="w-full"
      />
      <div className="flex rounded-md border bg-background p-0.5">
        {LOG_TYPE_FILTERS.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => onLogTypeChange(item)}
            className={cn(
              'h-8 whitespace-nowrap rounded-sm px-3 text-sm text-muted-foreground transition-colors hover:text-foreground',
              filters.logType === item && 'bg-muted text-foreground shadow-xs',
            )}
          >
            {t(`filters.${item}`)}
          </button>
        ))}
      </div>
      <Button type="submit" disabled={isFetching}>
        <SearchIcon />
        {t('query')}
      </Button>
      <Button
        type="button"
        variant="outline"
        onClick={onReset}
        disabled={isFetching}
      >
        <RotateCcwIcon />
        {t('reset')}
      </Button>
      <Button
        type="button"
        variant="outline"
        onClick={onRefresh}
        disabled={isFetching || zeroResult}
      >
        <RefreshCwIcon className={cn(isFetching && 'animate-spin')} />
        {t('refresh')}
      </Button>
    </form>
  )
}
