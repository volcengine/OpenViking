import { Link } from '@tanstack/react-router'
import {
  Brain,
  FileText,
  FolderOpen,
  Loader2,
  SearchIcon,
  Upload,
  Workflow,
  Wrench,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import type { TFunction } from 'i18next'

import { Button } from '#/components/ui/button'
import { cn } from '#/lib/utils'
import type { FindContextType, FindQueryPlanItem } from '#/lib/retrieval'

import { LoadingHint } from './loading-hint'
import { MemoryDetailSheet } from './memory-detail-sheet'
import type { MemoryDetail } from './memory-detail-sheet'
import { RecallResults } from './recall-results'
import type { RetrievalQueryResult } from '../-hooks/use-retrieval-query'
import {
  displayName,
  flattenResults,
  memoryTypeFromUri,
  resourceSearchForResult,
} from '../-lib/results'
import type { FlatRetrievalItem } from '../-types/retrieval'

const TYPE_META: Record<
  FindContextType,
  { icon: typeof Brain; color: string; bgColor: string }
> = {
  resource: {
    icon: FileText,
    color: 'text-blue-500',
    bgColor: 'bg-blue-500/15',
  },
  memory: { icon: Brain, color: 'text-amber-500', bgColor: 'bg-amber-500/15' },
  skill: {
    icon: Wrench,
    color: 'text-emerald-500',
    bgColor: 'bg-emerald-500/15',
  },
}

export function RetrievalResults({
  data,
  hasRetrievableContext,
  hasSubmitted,
  isCheckingContext,
  isError,
  isLoading,
  onUploadClick,
  resultCount,
  t,
}: {
  data?: RetrievalQueryResult
  hasRetrievableContext: boolean
  hasSubmitted: boolean
  isCheckingContext: boolean
  isError: boolean
  isLoading: boolean
  onUploadClick: () => void
  resultCount: number
  t: TFunction<'retrieval'>
}) {
  const [memoryDetail, setMemoryDetail] = useState<MemoryDetail | null>(null)
  const flatItems = useMemo(
    () => (data?.kind === 'results' ? flattenResults(data.result) : []),
    [data],
  )
  const queryPlanItems =
    data?.kind === 'results' ? (data.result.query_plan?.queries ?? []) : []
  const total =
    data?.kind === 'recall' ? data.result.entries.length : flatItems.length
  const hasResults = total > 0

  return (
    <>
      <div className="flex flex-col gap-3">
        <h2 className="text-base font-medium">
          {hasSubmitted && hasResults
            ? data?.kind === 'recall'
              ? t('recall.resultTitle', { count: total })
              : t('results.topN', {
                  count: Math.min(flatItems.length, resultCount),
                })
            : t('results.title')}
        </h2>

        <div className="min-h-80 overflow-hidden rounded-lg border border-dashed bg-card/50">
          {!hasSubmitted ? (
            <EmptyRetrievalState
              hasRetrievableContext={hasRetrievableContext}
              isCheckingContext={isCheckingContext}
              onUploadClick={onUploadClick}
              t={t}
            />
          ) : isLoading ? (
            <LoadingHint />
          ) : isError ? (
            <div className="flex min-h-80 items-center justify-center text-sm text-destructive">
              {t('error')}
            </div>
          ) : !hasResults ? (
            <div className="flex min-h-80 flex-col items-center justify-center gap-2 text-center">
              <SearchIcon className="size-8 text-muted-foreground/25" />
              <p className="text-sm text-muted-foreground/60">
                {t('noResults.title')}
              </p>
              <p className="text-xs text-muted-foreground/40">
                {t('noResults.subtitle')}
              </p>
            </div>
          ) : data?.kind === 'recall' ? (
            <RecallResults
              onSelect={setMemoryDetail}
              result={data.result}
              t={t}
            />
          ) : (
            <ResultList
              flatItems={flatItems}
              onSelectMemory={setMemoryDetail}
              queryPlanItems={queryPlanItems}
              t={t}
            />
          )}
        </div>
      </div>

      <MemoryDetailSheet
        detail={memoryDetail}
        onClose={() => setMemoryDetail(null)}
        t={t}
      />
    </>
  )
}

function EmptyRetrievalState({
  hasRetrievableContext,
  isCheckingContext,
  onUploadClick,
  t,
}: {
  hasRetrievableContext: boolean
  isCheckingContext: boolean
  onUploadClick: () => void
  t: TFunction<'retrieval'>
}) {
  return (
    <div className="flex min-h-80 flex-col items-center justify-center gap-3 text-center">
      {isCheckingContext ? (
        <>
          <Loader2 className="size-8 animate-spin text-muted-foreground/30" />
          <p className="text-sm text-muted-foreground">{t('empty.checking')}</p>
        </>
      ) : hasRetrievableContext ? (
        <>
          <SearchIcon className="size-10 text-muted-foreground/25" />
          <p className="text-sm text-muted-foreground">
            {t('empty.readyTitle')}
          </p>
          <p className="text-xs text-muted-foreground/60">
            {t('empty.readyDescription')}
          </p>
        </>
      ) : (
        <>
          <SearchIcon className="size-10 text-muted-foreground/25" />
          <p className="text-sm text-muted-foreground">{t('empty.title')}</p>
          <p className="text-xs text-muted-foreground/60">
            {t('empty.description')}
          </p>
          <Button
            className="mt-1 gap-1.5"
            onClick={onUploadClick}
            size="sm"
            variant="secondary"
          >
            <Upload className="size-4" />
            {t('empty.upload')}
          </Button>
        </>
      )}
    </div>
  )
}

function ResultList({
  flatItems,
  onSelectMemory,
  queryPlanItems,
  t,
}: {
  flatItems: FlatRetrievalItem[]
  onSelectMemory: (detail: MemoryDetail) => void
  queryPlanItems: FindQueryPlanItem[]
  t: TFunction<'retrieval'>
}) {
  return (
    <div className="divide-y">
      {queryPlanItems.length > 0 ? (
        <div className="border-b bg-muted/20 px-4 py-3">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Workflow className="size-3.5" />
            <span>
              {t('queryPlan.title', { count: queryPlanItems.length })}
            </span>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {queryPlanItems.slice(0, 4).map((plan, index) => (
              <span
                className="inline-flex max-w-full items-center gap-1 rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground"
                key={`${plan.query}-${index}`}
              >
                {plan.context_type ? (
                  <span
                    className={cn(
                      'font-medium',
                      TYPE_META[plan.context_type].color,
                    )}
                  >
                    {t(`types.${plan.context_type}`)}
                  </span>
                ) : null}
                <span className="truncate">{plan.query}</span>
              </span>
            ))}
            {queryPlanItems.length > 4 ? (
              <span className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
                {t('queryPlan.more', { count: queryPlanItems.length - 4 })}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
      {flatItems.map((item) => (
        <ResultRow
          item={item}
          key={`${item.item.uri}-${item.flatIndex}`}
          onSelectMemory={onSelectMemory}
          t={t}
        />
      ))}
    </div>
  )
}

function ResultRow({
  item,
  onSelectMemory,
  t,
}: {
  item: FlatRetrievalItem
  onSelectMemory: (detail: MemoryDetail) => void
  t: TFunction<'retrieval'>
}) {
  const row = <ResultRowContent item={item} t={t} />

  if (item.type === 'memory') {
    return (
      <button
        className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-inset"
        onClick={() =>
          onSelectMemory({
            abstract: item.item.abstract,
            item: item.item,
            memoryType: memoryTypeFromUri(item.item.uri) ?? 'MEMORY',
            score: item.item.score,
            uri: item.item.uri,
          })
        }
        type="button"
      >
        {row}
      </button>
    )
  }

  return (
    <Link
      className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-inset"
      rel="noreferrer noopener"
      search={resourceSearchForResult(item.item)}
      target="_blank"
      to="/playground"
    >
      {row}
    </Link>
  )
}

function ResultRowContent({
  item,
  t,
}: {
  item: FlatRetrievalItem
  t: TFunction<'retrieval'>
}) {
  const { name, parent } = displayName(item.item.uri)
  const meta = TYPE_META[item.type]
  const Icon = meta.icon
  const memoryType =
    item.type === 'memory' ? memoryTypeFromUri(item.item.uri) : undefined

  return (
    <>
      <div
        className={cn(
          'mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold uppercase tracking-wide',
          meta.bgColor,
          meta.color,
        )}
      >
        <Icon className="size-3" />
        <span>{t(`types.${item.type}`)}</span>
      </div>
      {memoryType ? (
        <span className="mt-0.5 rounded-md border bg-background px-2 py-1 font-mono text-[10px] font-medium text-muted-foreground">
          {memoryType}
        </span>
      ) : null}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{name}</div>
        <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground/70">
          <FolderOpen className="size-3 shrink-0" />
          <span className="truncate">{parent}</span>
        </div>
        {item.item.abstract ? (
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground/60">
            {item.item.abstract}
          </p>
        ) : null}
      </div>
      {item.item.result_kind === 'grep' && item.item.line !== undefined ? (
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
          {t('results.line', { line: item.item.line })}
        </span>
      ) : item.item.result_kind !== 'glob' ? (
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-muted-foreground">
          {item.item.score.toFixed(3)}
        </span>
      ) : null}
    </>
  )
}
