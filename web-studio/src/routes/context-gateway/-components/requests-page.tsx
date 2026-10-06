import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link, getRouteApi, useNavigate } from '@tanstack/react-router'
import {
  ActivityIcon,
  CircleAlertIcon,
  PlugIcon,
  RefreshCcwIcon,
  RefreshCwIcon,
  SearchIcon,
  SearchXIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Alert, AlertDescription } from '#/components/ui/alert'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardHeader } from '#/components/ui/card'
import { Input } from '#/components/ui/input'
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from '#/components/ui/pagination'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { Table, TableBody } from '#/components/ui/table'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'
import { cn } from '#/lib/utils'

import { LOG_KINDS, resyncCapture } from '../-lib/api'
import type { GatewayKey, LogKind, LogRecord, Upstream } from '../-lib/api'
import { formatClockTime, formatNumber } from '../-lib/format'
import { gatewayErrorMessage, kindLabel } from '../-lib/localize'
import type { RequestsFilter } from '../-lib/search'
import { useGateway, useKeys, useLogs, useUpstreams } from '../-lib/use-gateway'
import { ConfirmDialog } from './confirm-dialog'
import { EmptyState, ErrorState, LoadingState } from './empty-state'
import type { ResyncRequest } from './request-details'
import { RequestRow, RequestsTableHeader } from './requests-row'
import { SectionHeader } from './section-header'

const route = getRouteApi('/context-gateway/requests')

/** The gateway returns at most this many records; filtering is client-side. */
const LOG_LIMIT = 1000
const PAGE_SIZE = 25
const REFRESH_INTERVAL_MS = 30_000

type Segment = RequestsFilter | 'all'

const SEGMENTS: Segment[] = ['all', 'messages', 'tools', 'issues']

/** Segments that are shortcuts for one request type. */
const SEGMENT_KINDS: Partial<Record<Segment, LogKind>> = {
  messages: 'user',
  tools: 'continuation',
}

type KindFilter = LogKind | 'all'

/** Degraded, saving retrying or paused, or an HTTP error. */
function hasIssue(record: LogRecord): boolean {
  return (
    Boolean(record.degradation) ||
    record.capture_status === 'retrying' ||
    record.capture_status === 'paused' ||
    (record.status ?? 0) >= 400
  )
}

function inSegment(record: LogRecord, segment: Segment): boolean {
  if (segment === 'all') return true
  if (segment === 'issues') return hasIssue(record)
  return record.kind === SEGMENT_KINDS[segment]
}

/** Text a search term is matched against: model, conversation, key, upstream. */
function searchableText(
  record: LogRecord,
  upstreams?: Map<string, Upstream>,
  keys?: Map<string, GatewayKey>,
): string {
  const key = record.credential_id ? keys?.get(record.credential_id) : undefined
  const upstream = record.upstream_id
    ? upstreams?.get(record.upstream_id)
    : undefined
  return [
    record.model,
    record.session,
    record.request_id,
    key?.name,
    key?.prefix,
    upstream?.name,
  ]
    .filter(Boolean)
    .join('\n')
    .toLowerCase()
}

/**
 * The gateway logs a client's conversation id (`X-OpenViking-Session`, a chat
 * id…) as its SHA-256, so a pasted id is matched through the same hash.
 * Undefined where Web Crypto is missing (Studio over plain HTTP).
 */
async function conversationHash(value: string): Promise<string | undefined> {
  // The types promise `subtle`, but browsers expose it on HTTPS and localhost only.
  const subtle = (globalThis.crypto as Partial<Crypto> | undefined)?.subtle
  if (!value || !subtle) return undefined
  const digest = await subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('')
}

function byId<T extends { id: string }>(items?: T[]) {
  return items ? new Map(items.map((item) => [item.id, item])) : undefined
}

/** Stable row keys; memory-sync records have no request id. */
function rowKeys(records: LogRecord[]): string[] {
  const seen = new Map<string, number>()
  return records.map((record) => {
    const base =
      record.request_id ??
      [
        record.kind,
        record.time,
        record.session,
        record.capture_status,
        record.capture_reason,
      ].join(':')
    const count = seen.get(base) ?? 0
    seen.set(base, count + 1)
    return count ? `${base}#${count}` : base
  })
}

/** Request log (`?filter=messages|tools|issues`). */
export function RequestsPage() {
  const { t, i18n } = useTranslation('contextGateway')
  const locale = i18n.resolvedLanguage
  const navigate = useNavigate()
  const search = route.useSearch()
  const segment: Segment = search.filter ?? 'all'
  const [kind, setKind] = React.useState<KindFilter>('all')
  const [query, setQuery] = React.useState('')
  const deferredQuery = React.useDeferredValue(query)
  const [hashed, setHashed] = React.useState<{ query: string; hash: string }>()
  const [page, setPage] = React.useState(1)
  const [resyncTarget, setResyncTarget] = React.useState<ResyncRequest | null>(
    null,
  )

  const { connection } = useGateway()
  // Polls only while the browser tab is visible (React Query's default) and on
  // the first page, so rows don't shift while someone reads older ones.
  const logs = useLogs(LOG_LIMIT, {
    refetchInterval: page === 1 ? REFRESH_INTERVAL_MS : false,
  })
  const upstreams = useUpstreams()
  const keys = useKeys()
  const upstreamsById = React.useMemo(
    () => byId(upstreams.data),
    [upstreams.data],
  )
  const keysById = React.useMemo(() => byId(keys.data), [keys.data])

  React.useEffect(() => {
    const value = deferredQuery.trim()
    let current = true
    void conversationHash(value).then((hash) => {
      if (current && hash) setHashed({ query: value, hash })
    })
    return () => {
      current = false
    }
  }, [deferredQuery])

  const records = React.useMemo(() => logs.data ?? [], [logs.data])
  const keyList = React.useMemo(() => rowKeys(records), [records])
  const counts = React.useMemo(
    () =>
      Object.fromEntries(
        SEGMENTS.map((item) => [
          item,
          records.filter((record) => inSegment(record, item)).length,
        ]),
      ) as Record<Segment, number>,
    [records],
  )
  const filtered = React.useMemo(() => {
    const term = deferredQuery.trim()
    const needle = term.toLowerCase()
    const session = hashed?.query === term ? hashed.hash : undefined
    return records
      .map((record, index) => ({ record, key: keyList[index] }))
      .filter(
        ({ record }) =>
          inSegment(record, segment) &&
          (kind === 'all' || record.kind === kind) &&
          (!needle ||
            (session !== undefined && record.session === session) ||
            searchableText(record, upstreamsById, keysById).includes(needle)),
      )
  }, [
    records,
    keyList,
    segment,
    kind,
    deferredQuery,
    hashed,
    upstreamsById,
    keysById,
  ])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount)
  const visible = filtered.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  )

  const resync = useMutation({
    mutationFn: ({ keyId, ...target }: ResyncRequest) =>
      resyncCapture(connection, keyId, target),
    onSuccess: () => toast.success(t('requests.resync.done')),
    onError: (error) => toast.error(gatewayErrorMessage(t, error)),
  })

  function showSegment(next: Segment) {
    setPage(1)
    if (SEGMENT_KINDS[next]) setKind('all')
    void navigate({
      to: '/context-gateway/requests',
      search: next === 'all' ? {} : { filter: next },
      replace: true,
    })
  }

  function showKind(next: KindFilter) {
    setPage(1)
    setKind(next)
    // A type shortcut and a different type would never match together.
    if (next !== 'all' && SEGMENT_KINDS[segment]) showSegment('all')
  }

  function clearFilters() {
    setQuery('')
    setKind('all')
    showSegment('all')
  }

  function refresh() {
    void logs.refetch()
    void upstreams.refetch()
    void keys.refetch()
  }

  let body: React.ReactNode
  if (logs.isPending) {
    body = <LoadingState />
  } else if (!logs.data) {
    body = (
      <ErrorState
        title={t('requests.loadFailed')}
        error={logs.error}
        retrying={logs.isFetching}
        onRetry={refresh}
      />
    )
  } else if (records.length === 0) {
    body = (
      <EmptyState
        icon={<ActivityIcon />}
        title={t('requests.empty.title')}
        description={t('requests.empty.description')}
        action={
          <Button
            size="sm"
            nativeButton={false}
            render={<Link to="/context-gateway/connect" />}
          >
            <PlugIcon />
            {t('requests.empty.action')}
          </Button>
        }
      />
    )
  } else if (filtered.length === 0) {
    body = (
      <EmptyState
        icon={<SearchXIcon />}
        title={t('requests.noMatch.title')}
        description={t('requests.noMatch.description')}
        action={
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={clearFilters}
          >
            {t('requests.filters.clear')}
          </Button>
        }
      />
    )
  } else {
    body = (
      <>
        <Table>
          <RequestsTableHeader />
          <TableBody>
            {visible.map(({ record, key }) => (
              <RequestRow
                key={key}
                record={record}
                upstreams={upstreamsById}
                keys={keysById}
                onResync={setResyncTarget}
              />
            ))}
          </TableBody>
        </Table>
        <RequestsFooter
          page={currentPage}
          pageCount={pageCount}
          total={filtered.length}
          onPageChange={setPage}
        />
      </>
    )
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <SectionHeader description={t('requests.description')} />
      <Card className="gap-0 overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/20 px-4 py-3 [.border-b]:pb-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <div
              role="group"
              aria-label={t('requests.filters.label')}
              className="flex max-w-full overflow-x-auto rounded-md border bg-background p-0.5 [scrollbar-width:none]"
            >
              {SEGMENTS.map((item) => (
                <button
                  key={item}
                  type="button"
                  aria-pressed={segment === item}
                  onClick={() => showSegment(item)}
                  className={cn(
                    'flex h-8 items-center gap-1.5 rounded-sm px-3 text-sm whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                    segment === item && 'bg-muted text-foreground shadow-xs',
                  )}
                >
                  {t(`requests.filters.${item}`)}
                  {logs.data ? (
                    <span
                      className={cn(
                        'text-xs tabular-nums',
                        item === 'issues' && counts.issues > 0
                          ? 'font-medium text-amber-700 dark:text-amber-300'
                          : 'text-muted-foreground',
                      )}
                    >
                      {formatNumber(counts[item], locale)}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
            <Select
              value={kind}
              onValueChange={(value) => {
                if (value) showKind(value as KindFilter)
              }}
            >
              <SelectTrigger
                className="h-9 w-36 shrink-0 bg-background"
                aria-label={t('requests.filters.kind')}
              >
                <SelectValue>
                  {kind === 'all'
                    ? t('requests.filters.allKinds')
                    : kindLabel(t, kind)}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">
                  {t('requests.filters.allKinds')}
                </SelectItem>
                {LOG_KINDS.map((item) => (
                  <SelectItem key={item} value={item}>
                    {kindLabel(t, item)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {/* Fills the toolbar; on phones it takes its own row below Refresh. */}
            <div className="relative order-2 min-w-40 flex-1 basis-full sm:order-none sm:basis-40">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                {...PLAIN_INPUT_PROPS}
                type="search"
                value={query}
                onChange={(event) => {
                  setPage(1)
                  setQuery(event.target.value)
                }}
                placeholder={t('requests.filters.search')}
                aria-label={t('requests.filters.search')}
                className="bg-background pl-8"
              />
            </div>
            <div className="order-1 ml-auto flex shrink-0 items-center gap-2 sm:order-none">
              {logs.dataUpdatedAt ? (
                <span className="hidden text-xs whitespace-nowrap text-muted-foreground tabular-nums sm:inline">
                  {t('states.updatedAt', {
                    time: formatClockTime(logs.dataUpdatedAt, locale),
                  })}
                </span>
              ) : null}
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={logs.isFetching}
                onClick={refresh}
              >
                <RefreshCwIcon
                  className={cn(logs.isFetching && 'animate-spin')}
                />
                {t('actions.refresh')}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {logs.isError && logs.data ? (
            <div className="border-b p-3">
              <Alert variant="destructive">
                <CircleAlertIcon />
                <AlertDescription>
                  {t('requests.refreshFailed', {
                    message: gatewayErrorMessage(t, logs.error),
                  })}
                </AlertDescription>
              </Alert>
            </div>
          ) : null}
          {body}
        </CardContent>
      </Card>
      <ConfirmDialog
        open={resyncTarget !== null}
        onOpenChange={(open) => {
          if (!open) setResyncTarget(null)
        }}
        title={t('requests.resync.title')}
        description={t('requests.resync.description')}
        confirmLabel={t('requests.resync.confirm')}
        icon={<RefreshCcwIcon />}
        destructive={false}
        onConfirm={() =>
          resyncTarget ? resync.mutateAsync(resyncTarget) : undefined
        }
      />
    </div>
  )
}

type RequestsFooterProps = {
  page: number
  pageCount: number
  total: number
  onPageChange: (page: number) => void
}

/** Record count, search scope and page links (five-page window). */
function RequestsFooter({
  page,
  pageCount,
  total,
  onPageChange,
}: RequestsFooterProps) {
  const { t, i18n } = useTranslation('contextGateway')
  const locale = i18n.resolvedLanguage
  const start = Math.max(1, Math.min(page - 2, pageCount - 4))
  const pages = Array.from(
    { length: Math.min(5, pageCount) },
    (_, index) => start + index,
  )

  function go(event: React.MouseEvent, target: number) {
    event.preventDefault()
    if (target >= 1 && target <= pageCount) onPageChange(target)
  }

  return (
    <div className="flex flex-col gap-3 border-t px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="grid gap-0.5 text-center sm:text-left">
        <p className="text-sm text-muted-foreground">
          {t('requests.pagination.summary', {
            total: formatNumber(total, locale),
            page,
            pageCount,
          })}
        </p>
        <p className="text-xs text-muted-foreground">
          {t('requests.scope', { count: formatNumber(LOG_LIMIT, locale) })}
        </p>
      </div>
      {pageCount > 1 ? (
        <Pagination className="mx-0 w-auto justify-center sm:justify-end">
          <PaginationContent>
            <PaginationItem>
              <PaginationPrevious
                href="#"
                aria-disabled={page <= 1}
                className={cn(page <= 1 && 'pointer-events-none opacity-50')}
                onClick={(event) => go(event, page - 1)}
              />
            </PaginationItem>
            {pages.map((item) => (
              <PaginationItem key={item}>
                <PaginationLink
                  href="#"
                  isActive={item === page}
                  onClick={(event) => go(event, item)}
                >
                  {item}
                </PaginationLink>
              </PaginationItem>
            ))}
            <PaginationItem>
              <PaginationNext
                href="#"
                aria-disabled={page >= pageCount}
                className={cn(
                  page >= pageCount && 'pointer-events-none opacity-50',
                )}
                onClick={(event) => go(event, page + 1)}
              />
            </PaginationItem>
          </PaginationContent>
        </Pagination>
      ) : null}
    </div>
  )
}
