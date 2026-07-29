import {
  Brain,
  CircleAlert,
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
import type {
  FindContextType,
  FindQueryPlanItem,
  GroupedFindResult,
} from '#/lib/retrieval'

import { LoadingHint } from './loading-hint'
import { RetrievalDetailSheet } from './retrieval-detail-sheet'
import type { RetrievalDetail } from './retrieval-detail-sheet'
import { displayName, flattenResults, memoryTypeFromUri } from '../-lib/results'
import type { FlatRetrievalItem } from '../-types/retrieval'

const TYPE_META: Record<
  FindContextType,
  { icon: typeof Brain; color: string }
> = {
  resource: {
    icon: FileText,
    color: 'text-blue-500',
  },
  memory: { icon: Brain, color: 'text-amber-500' },
  skill: {
    icon: Wrench,
    color: 'text-emerald-500',
  },
}

export function RetrievalResults({
  data,
  error,
  hasRetrievableContext,
  hasSubmitted,
  isCheckingContext,
  isError,
  isLoading,
  onUploadClick,
  resultCount,
  t,
}: {
  data?: GroupedFindResult
  error?: unknown
  hasRetrievableContext: boolean
  hasSubmitted: boolean
  isCheckingContext: boolean
  isError: boolean
  isLoading: boolean
  onUploadClick: () => void
  resultCount: number
  t: TFunction<'retrieval'>
}) {
  const [detail, setDetail] = useState<RetrievalDetail | null>(null)
  const flatItems = useMemo(() => (data ? flattenResults(data) : []), [data])
  const queryPlanItems = data?.query_plan?.queries ?? []
  const provenance = data?.provenance ?? []
  const hasResults = flatItems.length > 0

  return (
    <>
      <div className="flex flex-col gap-3">
        <h2 className="text-base font-medium">
          {hasSubmitted && hasResults
            ? t('results.topN', {
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
            <RetrievalError error={error} t={t} />
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
          ) : (
            <ResultList
              flatItems={flatItems}
              onSelect={setDetail}
              provenance={provenance}
              queryPlanItems={queryPlanItems}
              t={t}
            />
          )}
        </div>
      </div>

      <RetrievalDetailSheet
        detail={detail}
        onClose={() => setDetail(null)}
        t={t}
      />
    </>
  )
}

function RetrievalError({
  error,
  t,
}: {
  error?: unknown
  t: TFunction<'retrieval'>
}) {
  const details = getErrorDetails(error, t)

  return (
    <div className="flex min-h-80 items-center justify-center px-6 py-10">
      <div className="flex max-w-xl flex-col items-center text-center">
        <div className="flex size-10 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <CircleAlert className="size-5" />
        </div>
        <h3 className="mt-3 text-sm font-medium text-foreground">
          {t('error.title')}
        </h3>
        <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
          {details.message}
        </p>
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          {details.code ? (
            <ErrorMeta label={t('error.code')} value={details.code} />
          ) : null}
          {details.statusCode ? (
            <ErrorMeta
              label={t('error.status')}
              value={String(details.statusCode)}
            />
          ) : null}
          {details.requestId ? (
            <ErrorMeta label={t('error.requestId')} value={details.requestId} />
          ) : null}
        </div>
      </div>
    </div>
  )
}

function getErrorDetails(error: unknown, t: TFunction<'retrieval'>) {
  if (!error || typeof error !== 'object') {
    return { message: t('error.fallback') }
  }

  const data = error as Record<string, unknown>
  const code = typeof data.code === 'string' ? data.code : undefined
  const rawMessage =
    typeof data.message === 'string' && data.message.trim()
      ? data.message.trim()
      : undefined

  return {
    code,
    message:
      code === 'NETWORK_ERROR'
        ? t('error.network')
        : (rawMessage ?? t('error.fallback')),
    requestId: typeof data.requestId === 'string' ? data.requestId : undefined,
    statusCode:
      typeof data.statusCode === 'number' ? data.statusCode : undefined,
  }
}

function ErrorMeta({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded-md border bg-muted/30 px-2 py-1 font-mono text-[10px] text-muted-foreground">
      {label}: <span className="text-foreground">{value}</span>
    </span>
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
  onSelect,
  provenance,
  queryPlanItems,
  t,
}: {
  flatItems: FlatRetrievalItem[]
  onSelect: (detail: RetrievalDetail) => void
  provenance: NonNullable<GroupedFindResult['provenance']>
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
      {provenance.length > 0 ? (
        <details className="border-b bg-muted/10 px-4 py-3">
          <summary className="flex cursor-pointer items-center gap-2 text-xs font-medium text-muted-foreground">
            <Workflow className="size-3.5" />
            {t('results.provenance', { count: provenance.length })}
          </summary>
          <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background/70 p-3 font-mono text-xs leading-5 text-muted-foreground">
            {JSON.stringify(provenance, null, 2)}
          </pre>
        </details>
      ) : null}
      {flatItems.map((item) => (
        <ResultRow
          item={item}
          key={`${item.item.uri}-${item.flatIndex}`}
          onSelect={onSelect}
          t={t}
        />
      ))}
    </div>
  )
}

function ResultRow({
  item,
  onSelect,
  t,
}: {
  item: FlatRetrievalItem
  onSelect: (detail: RetrievalDetail) => void
  t: TFunction<'retrieval'>
}) {
  const row = <ResultRowContent item={item} t={t} />

  return (
    <button
      className="group w-full px-5 py-4 text-left transition-colors hover:bg-muted/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-inset"
      onClick={() =>
        onSelect({
          abstract: item.item.abstract,
          contextType: item.type,
          item: item.item,
          memoryType:
            item.type === 'memory'
              ? (memoryTypeFromUri(item.item.uri) ?? 'MEMORY')
              : undefined,
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

function ResultRowContent({
  item,
  t,
}: {
  item: FlatRetrievalItem
  t: TFunction<'retrieval'>
}) {
  const { name } = displayName(item.item.uri)
  const meta = TYPE_META[item.type]
  const Icon = meta.icon
  const memoryType =
    item.type === 'memory' ? memoryTypeFromUri(item.item.uri) : undefined

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-x-5">
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2.5">
          <Icon
            aria-hidden="true"
            className={cn('size-3.5 shrink-0', meta.color)}
          />
          <h3 className="min-w-0 truncate text-sm font-semibold text-foreground">
            {name}
          </h3>
          <span className="sr-only">{t(`types.${item.type}`)}</span>
          {memoryType ? (
            <span className="shrink-0 rounded border border-border/60 px-1.5 py-0.5 font-mono text-[9px] font-medium tracking-wide text-muted-foreground/70">
              {memoryType}
            </span>
          ) : null}
          <span
            className="shrink-0 font-mono text-[10px] font-medium tabular-nums text-muted-foreground/45"
            title={`${t('detail.level')}: L${item.item.level}`}
          >
            L{item.item.level}
          </span>
        </div>

        <dl className="mt-2.5 grid min-w-0 grid-cols-[3.25rem_minmax(0,1fr)] gap-x-2.5 gap-y-1.5">
          <dt className="pt-px text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground/55">
            {t('results.uri')}
          </dt>
          <dd
            className="flex min-w-0 items-center gap-1.5 font-mono text-[11px] text-muted-foreground/75"
            title={item.item.uri}
          >
            <FolderOpen className="size-3 shrink-0 text-muted-foreground/45" />
            <span className="truncate">{item.item.uri}</span>
          </dd>

          {item.item.abstract ? (
            <>
              <dt className="pt-0.5 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground/55">
                {t('results.description')}
              </dt>
              <dd className="line-clamp-2 text-xs leading-5 text-muted-foreground/70">
                {item.item.abstract}
              </dd>
            </>
          ) : null}
        </dl>
      </div>

      {item.item.result_kind === 'grep' && item.item.line !== undefined ? (
        <ResultMetric
          label={t('results.lineLabel')}
          value={String(item.item.line)}
        />
      ) : item.item.result_kind !== 'glob' ? (
        <ResultMetric
          label={t('results.score')}
          value={item.item.score.toFixed(3)}
        />
      ) : null}
    </div>
  )
}

function ResultMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-16 self-start rounded-md border border-border/60 bg-muted/35 px-2.5 py-2 text-right transition-colors group-hover:bg-background/70">
      <div className="text-[9px] font-medium uppercase tracking-[0.1em] text-muted-foreground/50">
        {label}
      </div>
      <div className="mt-0.5 font-mono text-xs font-semibold tabular-nums text-foreground/75">
        {value}
      </div>
    </div>
  )
}
