import { useIsFetching } from '@tanstack/react-query'
import {
  ActivityIcon,
  BrainIcon,
  RefreshCwIcon,
  RepeatIcon,
  ZapIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { Card } from '#/components/ui/card'

import type { CacheStats, LogRecord, Overview } from '../-lib/api'
import {
  EMPTY_VALUE,
  formatClockTime,
  formatCompact,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
  formatRelativeTime,
} from '../-lib/format'
import {
  gatewayQueryKey,
  useGateway,
  useLogs,
  useOverview,
} from '../-lib/use-gateway'
import { ErrorState, LoadingState } from './empty-state'
import { MetricCard } from './metric-card'
import { RecentRequests } from './overview-recent'
import { OverviewSetup } from './overview-setup'
import { DegradedRequestsCard, OpenVikingCard } from './overview-status'
import { SectionHeader } from './section-header'

/** Model requests shown under "Recent requests". */
const RECENT_LIMIT = 8
/**
 * Records fetched to find them: memory-sync events share the log and can
 * outnumber model requests while saving retries.
 */
const RECENT_FETCH = 50

/** The newest model requests; memory-sync events have their own status. */
function recentModelRequests(records: LogRecord[]): LogRecord[] {
  return records
    .filter((record) => record.kind !== 'capture')
    .slice(0, RECENT_LIMIT)
}

/** Cache hit rate, or a dash when no input tokens were counted. */
function hitRate(stats: CacheStats, locale?: string): string {
  return stats.input_tokens > 0
    ? formatPercent(stats.cache_hit_ratio, locale)
    : EMPTY_VALUE
}

function MetricRow({ data }: { data: Overview }) {
  const { t, i18n } = useTranslation('gateway')
  const locale = i18n.resolvedLanguage
  const { first_call: firstCall, continuation } = data.cache
  const icon = 'size-4'
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        icon={<ActivityIcon className={icon} />}
        label={t('overview.kpi.requests.label')}
        hint={t('overview.kpi.requests.hint')}
        value={formatNumber(data.requests, locale)}
        footnote={
          data.last_request_at != null ? (
            <>
              <span
                className="block"
                title={formatDateTime(data.last_request_at, locale)}
              >
                {t('overview.kpi.requests.last', {
                  time: formatRelativeTime(data.last_request_at, locale),
                })}
              </span>
              <span className="block">
                {t('overview.kpi.requests.outputTokens', {
                  tokens: formatCompact(data.output_tokens, locale),
                })}
              </span>
            </>
          ) : (
            t('overview.kpi.requests.none')
          )
        }
      />
      <MetricCard
        emphasis
        icon={<ZapIcon className={icon} />}
        label={t('overview.kpi.firstCall.label')}
        hint={t('overview.kpi.firstCall.hint')}
        value={hitRate(firstCall, locale)}
        footnote={t('overview.kpi.firstCall.footnote', {
          count: firstCall.requests,
          number: formatNumber(firstCall.requests, locale),
        })}
      />
      <MetricCard
        icon={<RepeatIcon className={icon} />}
        label={t('overview.kpi.continuation.label')}
        hint={t('overview.kpi.continuation.hint')}
        value={hitRate(continuation, locale)}
        footnote={t('overview.kpi.continuation.footnote', {
          count: continuation.requests,
          number: formatNumber(continuation.requests, locale),
        })}
      />
      <MetricCard
        icon={<BrainIcon className={icon} />}
        label={t('overview.kpi.recall.label')}
        hint={t('overview.kpi.recall.hint')}
        value={formatNumber(data.recall_count, locale)}
        footnote={
          data.recall_requests > 0
            ? t('overview.kpi.recall.footnote', {
                count: data.recall_requests,
                number: formatNumber(data.recall_requests, locale),
                duration: formatDuration(data.recall_ms, locale),
              })
            : t('overview.kpi.recall.none')
        }
      />
    </div>
  )
}

/** Gateway health, usage and recent requests, with a setup checklist until the first request. */
export function OverviewPage() {
  const { t, i18n } = useTranslation('gateway')
  const locale = i18n.resolvedLanguage
  const { scope, invalidate } = useGateway()
  const overview = useOverview()
  const logs = useLogs(RECENT_FETCH, { select: recentModelRequests })
  const fetching = useIsFetching({ queryKey: gatewayQueryKey(scope) }) > 0
  const refresh = () =>
    void invalidate('overview', 'logs', 'upstreams', 'profiles', 'keys')
  const data = overview.data

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <SectionHeader
        description={t('overview.description')}
        actions={
          <>
            {overview.dataUpdatedAt ? (
              <span className="text-xs text-muted-foreground tabular-nums">
                {t('states.updatedAt', {
                  time: formatClockTime(overview.dataUpdatedAt, locale),
                })}
              </span>
            ) : null}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={fetching}
              onClick={refresh}
            >
              <RefreshCwIcon
                className={fetching ? 'animate-spin' : undefined}
              />
              {t('actions.refresh')}
            </Button>
          </>
        }
      />
      {data ? (
        <>
          {data.last_request_at == null ? <OverviewSetup /> : null}
          <MetricRow data={data} />
          <div className="grid gap-3 lg:grid-cols-2">
            <OpenVikingCard
              health={data.openviking}
              captureIssues={data.capture_issues}
            />
            <DegradedRequestsCard degradations={data.degradations} />
          </div>
          <RecentRequests logs={logs.data} error={logs.error} />
          <p className="text-xs text-muted-foreground">
            {t('overview.sampleNote', {
              limit: formatNumber(data.sample_limit, locale),
              count: data.log_retention_days,
            })}
          </p>
        </>
      ) : overview.isError ? (
        <Card className="py-0">
          <ErrorState
            title={t('overview.loadFailed')}
            error={overview.error}
            retrying={overview.isFetching}
            onRetry={() => void overview.refetch()}
          />
        </Card>
      ) : (
        <LoadingState />
      )}
    </div>
  )
}
