import type * as React from 'react'
import { RefreshCcwIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { cn } from '#/lib/utils'

import type { GatewayKey, LogRecord, ResyncTarget, Upstream } from '../-lib/api'
import {
  EMPTY_VALUE,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
  shortenId,
} from '../-lib/format'
import {
  captureReasonLabel,
  degradationInfo,
  recallReasonLabel,
  toolSkipReasonLabel,
  toolStopReasonLabel,
  windowReminderLabel,
} from '../-lib/localize'
import type { Translate } from '../-lib/localize'
import { CopyButton } from './copy-button'
import { recallFailed } from './memory-cell'
import { Notice } from './notice'
import { CaptureStatusBadge, ProtocolBadge } from './status-badges'

/** A conversation to resync and the key that used it. */
export type ResyncRequest = ResyncTarget & { keyId: string }

/** What a resync needs, when the record names a conversation and its key. */
function resyncRequest(record: LogRecord): ResyncRequest | undefined {
  const { session, protocol, credential_id: keyId } = record
  return session && protocol && keyId ? { keyId, session, protocol } : undefined
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground">{children}</span>
}

type TokenCounts = {
  input?: number
  cached?: number
  cacheWrite?: number
  output?: number
}

/** "Input 12,000 · cached 9,000 (75%) · cache write 0 · output 300". */
export function tokenLine(t: Translate, counts: TokenCounts, locale?: string) {
  const value = (count?: number) =>
    count === undefined ? EMPTY_VALUE : formatNumber(count, locale)
  const cached =
    counts.cached !== undefined && counts.input
      ? `${value(counts.cached)} (${formatPercent(counts.cached / counts.input, locale)})`
      : value(counts.cached)
  return t('requests.details.tokenLine', {
    input: value(counts.input),
    cached,
    cacheWrite: value(counts.cacheWrite),
    output: value(counts.output),
  })
}

function DetailItem({
  label,
  className,
  children,
}: {
  label: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={cn('grid min-w-0 content-start gap-1', className)}>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="grid min-w-0 gap-1 text-sm break-words">{children}</dd>
    </div>
  )
}

/** What went wrong with a request and what to do about it. */
function Callout({
  tone,
  title,
  happened,
  action,
}: {
  tone: 'warning' | 'danger'
  title?: string
  happened?: string
  action?: string
}) {
  const { t } = useTranslation('contextGateway')
  return (
    <Notice tone={tone} title={title}>
      <dl className="grid gap-x-4 gap-y-1.5 text-foreground sm:grid-cols-[8rem_minmax(0,1fr)]">
        {happened ? (
          <>
            <dt className="text-muted-foreground">
              {t('requests.details.whatHappened')}
            </dt>
            <dd>{happened}</dd>
          </>
        ) : null}
        {action ? (
          <>
            <dt className="text-muted-foreground">
              {t('requests.details.whatToDo')}
            </dt>
            <dd>{action}</dd>
          </>
        ) : null}
      </dl>
    </Notice>
  )
}

/** False when the upstream reported no usage, or only zeros. */
function hasTokens(record: LogRecord): boolean {
  return Boolean(
    record.input_tokens || record.output_tokens || record.cached_tokens,
  )
}

function TokenDetails({ record }: { record: LogRecord }) {
  const { t, i18n } = useTranslation('contextGateway')
  const locale = i18n.resolvedLanguage
  if (!hasTokens(record)) return <Muted>{t('requests.details.noUsage')}</Muted>
  const total = tokenLine(
    t,
    {
      input: record.input_tokens,
      cached: record.cached_tokens,
      cacheWrite: record.cache_write_tokens,
      output: record.output_tokens,
    },
    locale,
  )
  if (record.first_upstream_input_tokens === undefined) {
    return <span className="tabular-nums">{total}</span>
  }
  const first = tokenLine(
    t,
    {
      input: record.first_upstream_input_tokens,
      cached: record.first_upstream_cached_tokens,
      cacheWrite: record.first_upstream_cache_write_tokens,
      output: record.first_upstream_output_tokens,
    },
    locale,
  )
  const hidden = tokenLine(
    t,
    {
      input: record.hidden_upstream_input_tokens,
      cached: record.hidden_upstream_cached_tokens,
      cacheWrite: record.hidden_upstream_cache_write_tokens,
      output: record.hidden_upstream_output_tokens,
    },
    locale,
  )
  return (
    <div className="grid gap-1 tabular-nums">
      <span>{total}</span>
      <span className="text-xs text-muted-foreground">
        {t('requests.details.firstCall')}: {first}
      </span>
      <span className="text-xs text-muted-foreground">
        {t('requests.details.toolCalls', {
          count: record.hidden_upstream_calls ?? 0,
        })}
        : {hidden}
      </span>
    </div>
  )
}

type RequestDetailsProps = {
  record: LogRecord
  /** Upstreams by id; undefined until the list has loaded. */
  upstreams?: Map<string, Upstream>
  /** Gateway keys by id; undefined until the list has loaded. */
  keys?: Map<string, GatewayKey>
  onResync: (request: ResyncRequest) => void
}

/** Everything the log knows about one request, with what to do about problems. */
export function RequestDetails({
  record,
  upstreams,
  keys,
  onResync,
}: RequestDetailsProps) {
  const { t, i18n } = useTranslation('contextGateway')
  const locale = i18n.resolvedLanguage
  const upstream = record.upstream_id
    ? upstreams?.get(record.upstream_id)
    : undefined
  const gatewayKey = record.credential_id
    ? keys?.get(record.credential_id)
    : undefined
  const keyRevoked = Boolean(keys && record.credential_id && !gatewayKey)
  const isCapture = record.kind === 'capture'
  const degradation = record.degradation
    ? degradationInfo(t, record.degradation)
    : undefined
  const httpError =
    !degradation && record.status !== undefined && record.status >= 400
  const resync = resyncRequest(record)
  const hasTools = Boolean(
    record.tool_skip_reason ||
    record.tool_stop_reason ||
    (record.hidden_rounds ?? 0) > 0,
  )
  const hasContext = Boolean(
    record.context_window ||
    record.compaction_applied ||
    record.compaction_failed ||
    record.window,
  )

  return (
    <div className="grid gap-4">
      <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
        <DetailItem label={t('requests.details.time')}>
          <span className="tabular-nums">
            {formatDateTime(record.time, locale, true)}
          </span>
        </DetailItem>
        {/* Below `md` the table shows the model only in part. */}
        {record.model ? (
          <DetailItem label={t('requests.table.model')} className="md:hidden">
            <span className="font-mono text-xs break-all">{record.model}</span>
          </DetailItem>
        ) : null}
        {record.request_id ? (
          <DetailItem label={t('requests.details.requestId')}>
            <span className="flex min-w-0 items-center gap-1">
              <span
                className="truncate font-mono text-xs"
                title={record.request_id}
              >
                {record.request_id}
              </span>
              <CopyButton
                value={record.request_id}
                label={t('requests.details.copyRequestId')}
              />
            </span>
          </DetailItem>
        ) : null}
        {record.session ? (
          <DetailItem label={t('requests.details.conversation')}>
            <span className="flex min-w-0 items-center gap-1">
              <span
                className="truncate font-mono text-xs"
                title={record.session}
              >
                {shortenId(record.session, 16)}
              </span>
              <CopyButton
                value={record.session}
                label={t('requests.details.copyConversation')}
              />
            </span>
            {record.session.startsWith('anonymous-') ? (
              <span className="text-xs text-muted-foreground">
                {t('requests.details.anonymous')}
              </span>
            ) : null}
          </DetailItem>
        ) : null}
        {record.protocol ? (
          <DetailItem label={t('requests.details.protocol')}>
            <span>
              <ProtocolBadge protocol={record.protocol} />
            </span>
          </DetailItem>
        ) : null}
        {record.upstream_id ? (
          <DetailItem label={t('requests.details.upstream')}>
            {upstream ? (
              <span className="font-medium">{upstream.name}</span>
            ) : (
              <span
                className="text-muted-foreground"
                title={record.upstream_id}
              >
                {upstreams
                  ? t('requests.details.upstreamDeleted')
                  : shortenId(record.upstream_id)}
              </span>
            )}
          </DetailItem>
        ) : null}
        {record.credential_id ? (
          <DetailItem label={t('requests.details.key')}>
            {gatewayKey ? (
              <span className="flex min-w-0 items-baseline gap-2">
                <span className="truncate font-medium">{gatewayKey.name}</span>
                <span className="font-mono text-xs text-muted-foreground">
                  {gatewayKey.prefix}…
                </span>
              </span>
            ) : (
              <span
                className="text-muted-foreground"
                title={record.credential_id}
              >
                {keyRevoked
                  ? t('requests.details.keyRevoked')
                  : shortenId(record.credential_id)}
              </span>
            )}
          </DetailItem>
        ) : null}
        {record.duration_ms !== undefined ? (
          <DetailItem label={t('requests.details.duration')}>
            <span
              className="tabular-nums"
              title={t('requests.details.durationHint')}
            >
              {formatDuration(record.duration_ms, locale)}
            </span>
          </DetailItem>
        ) : null}
        {!isCapture ? (
          <DetailItem
            label={t('requests.details.tokens')}
            className="sm:col-span-2"
          >
            <TokenDetails record={record} />
          </DetailItem>
        ) : null}
        {record.cache_eligible !== undefined ? (
          <DetailItem label={t('requests.details.cache')}>
            {t(
              record.cache_eligible
                ? 'requests.details.cacheEligible'
                : 'requests.details.cacheIneligible',
              { min: formatNumber(record.cache_min_tokens ?? 0, locale) },
            )}
          </DetailItem>
        ) : null}
        {record.recall_reason || (record.replay_hits ?? 0) > 0 ? (
          <DetailItem label={t('requests.details.recall')}>
            {record.recall_reason ? (
              <span
                className={cn(
                  recallFailed(record) && 'text-amber-700 dark:text-amber-300',
                )}
              >
                {recallReasonLabel(t, record.recall_reason)}
                {record.recall_reason === 'recalled'
                  ? ` · ${t('requests.details.recallResult', {
                      count: record.recall_count ?? 0,
                      duration: formatDuration(record.recall_ms ?? 0, locale),
                    })}`
                  : null}
              </span>
            ) : null}
            {(record.replay_hits ?? 0) > 0 ? (
              <span className="text-xs text-muted-foreground">
                {t('requests.memory.replayed', { count: record.replay_hits })}
              </span>
            ) : null}
          </DetailItem>
        ) : null}
        {hasContext ? (
          <DetailItem label={t('requests.details.context')}>
            {record.context_window ? (
              <span className="tabular-nums">
                {t('requests.details.contextUsage', {
                  tokens: formatNumber(record.context_tokens ?? 0, locale),
                  window: formatNumber(record.context_window, locale),
                  percent: formatPercent(
                    (record.context_tokens ?? 0) / record.context_window,
                    locale,
                  ),
                })}
              </span>
            ) : null}
            {record.compaction_applied ? (
              <span>{t('requests.details.compactionApplied')}</span>
            ) : null}
            {record.compaction_tokens !== undefined ? (
              <span className="text-xs text-muted-foreground tabular-nums">
                {t('requests.details.compactionWritten', {
                  tokens: formatNumber(record.compaction_tokens, locale),
                  duration: formatDuration(record.compaction_ms ?? 0, locale),
                })}
              </span>
            ) : null}
            {record.compaction_failed ? (
              <span className="text-amber-700 dark:text-amber-300">
                {t('requests.details.compactionFailed', {
                  reason: record.compaction_failed,
                })}
              </span>
            ) : null}
            {record.window ? (
              <span>
                {t('requests.details.window', { number: record.window })}
              </span>
            ) : null}
            {record.window_reset ? (
              <span>{t('requests.details.windowReset')}</span>
            ) : null}
            {record.window_reminder ? (
              <span>{windowReminderLabel(t, record.window_reminder)}</span>
            ) : null}
          </DetailItem>
        ) : null}
        {record.capture_status ? (
          <DetailItem label={t('requests.details.saving')}>
            <span>
              <CaptureStatusBadge status={record.capture_status} />
            </span>
            {record.capture_reason ? (
              <span className="text-xs text-muted-foreground">
                {captureReasonLabel(t, record.capture_reason)}
              </span>
            ) : null}
            {record.capture_retry_at ? (
              <span className="text-xs text-muted-foreground">
                {t('requests.details.nextRetry', {
                  time: formatDateTime(record.capture_retry_at, locale),
                })}
              </span>
            ) : null}
          </DetailItem>
        ) : null}
        {hasTools ? (
          <DetailItem label={t('requests.details.tools')}>
            {record.tool_skip_reason ? (
              <span>
                {t('requests.details.toolsSkipped', {
                  reason: toolSkipReasonLabel(t, record.tool_skip_reason),
                })}
              </span>
            ) : null}
            {(record.hidden_rounds ?? 0) > 0 ? (
              <span className="tabular-nums">
                {t('requests.details.toolRounds', {
                  rounds: record.hidden_rounds,
                  calls: record.hidden_upstream_calls ?? 0,
                  tokens: formatNumber(record.hidden_added_tokens ?? 0, locale),
                })}
              </span>
            ) : null}
            {record.tool_stop_reason ? (
              <span className="text-amber-700 dark:text-amber-300">
                {toolStopReasonLabel(t, record.tool_stop_reason)}
              </span>
            ) : null}
          </DetailItem>
        ) : null}
      </dl>
      {degradation ? (
        <Callout
          tone="warning"
          title={degradation.label}
          happened={degradation.explanation}
          action={degradation.action}
        />
      ) : null}
      {httpError ? (
        <Callout
          tone="danger"
          happened={t('requests.details.httpError', { status: record.status })}
          action={t('requests.details.httpErrorAction')}
        />
      ) : null}
      {resync ? (
        <div className="flex flex-wrap items-center gap-3 border-t pt-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={keyRevoked}
            onClick={() => onResync(resync)}
          >
            <RefreshCcwIcon />
            {t('requests.resync.action')}
          </Button>
          <p className="max-w-2xl text-xs leading-5 text-muted-foreground">
            {keyRevoked
              ? t('requests.resync.keyRevoked')
              : t('requests.resync.hint')}
          </p>
        </div>
      ) : null}
    </div>
  )
}
