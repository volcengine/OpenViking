import { LoaderCircleIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { Spinner } from '#/components/ui/spinner'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'

import { formatTimestamp } from '../-lib/experience'
import type { TrajectoryItem } from '../-lib/types'

function TrajectoryRows({
  items,
  language,
  onSelect,
  selectLabel,
}: {
  items: TrajectoryItem[]
  language: string
  onSelect: (trajectory: TrajectoryItem) => void
  selectLabel: string
}) {
  return (
    <>
      {items.map((trajectory) => {
        const updated = formatTimestamp(
          trajectory.updated_at ?? trajectory.created_at,
          language,
        )

        return (
          <TableRow
            key={trajectory.uri}
            className="cursor-pointer"
            onClick={() => onSelect(trajectory)}
          >
            <TableCell className="max-w-0 py-2.5 pl-4">
              <button
                type="button"
                aria-label={selectLabel}
                className="block min-w-0 max-w-full truncate text-left font-medium underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                onClick={(event) => {
                  event.stopPropagation()
                  onSelect(trajectory)
                }}
              >
                {trajectory.name}
              </button>
              {trajectory.description ? (
                <span className="block truncate text-xs text-muted-foreground">
                  {trajectory.description}
                </span>
              ) : null}
            </TableCell>
            <TableCell className="w-40 py-2.5 pr-4 text-sm whitespace-nowrap text-muted-foreground">
              {updated ?? '-'}
            </TableCell>
          </TableRow>
        )
      })}
    </>
  )
}

/**
 * Applied-trajectory list with offset-based "load more" pagination.
 *
 * Clicking a row (or its name button) opens the trajectory content preview.
 */
export function TrajectoryList({
  errorMessage,
  hasMore,
  isLoading,
  isLoadingMore,
  items,
  language,
  onLoadMore,
  onRetry,
  onSelect,
  total,
}: {
  errorMessage?: string
  hasMore: boolean
  isLoading: boolean
  isLoadingMore: boolean
  items: TrajectoryItem[]
  language: string
  onLoadMore: () => void
  onRetry: () => void
  onSelect: (trajectory: TrajectoryItem) => void
  total: number
}) {
  const { t } = useTranslation('agentExperiencePage')

  if (isLoading) {
    return (
      <div className="flex min-h-24 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner className="size-3.5" />
        {t('detail.loadingMore')}
      </div>
    )
  }

  if (items.length === 0 && errorMessage) {
    return (
      <div className="grid min-h-24 place-items-center gap-2 text-center text-sm">
        <p className="text-muted-foreground">
          {t('detail.trajectoriesLoadFailed')}
        </p>
        <p className="max-w-md text-xs leading-5 text-muted-foreground/80">
          {errorMessage}
        </p>
        <Button type="button" size="xs" variant="outline" onClick={onRetry}>
          {t('refresh')}
        </Button>
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="grid min-h-24 place-items-center gap-1 px-4 py-4 text-center">
        <p className="text-sm text-muted-foreground">
          {t('detail.outcomeEmpty')}
        </p>
        <p className="max-w-md text-xs leading-5 text-muted-foreground/80">
          {t('detail.outcomeEmptyDescription')}
        </p>
      </div>
    )
  }

  return (
    <div className="grid gap-2">
      <div className="overflow-hidden rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="h-9 pl-4">
                {t('detail.trajectoriesTitle')}
              </TableHead>
              <TableHead className="h-9 w-40 pr-4 text-right">
                {t('columnUpdated')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TrajectoryRows
              items={items}
              language={language}
              onSelect={onSelect}
              selectLabel={t('detail.previewTitle')}
            />
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-center">
        {hasMore ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={isLoadingMore}
            onClick={onLoadMore}
          >
            {isLoadingMore ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : null}
            {isLoadingMore ? t('detail.loadingMore') : t('detail.loadMore')}
          </Button>
        ) : (
          <span className="text-xs text-muted-foreground">
            {t('detail.noMore', { count: total })}
          </span>
        )}
      </div>
    </div>
  )
}
