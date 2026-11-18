import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type {
  PerformanceObservation,
  PerformanceSummary,
  PerformanceWorkflow,
} from '@ov-server/api/v1/console'
import {
  EmptyState,
  Panel,
  SectionHeading,
} from '#/features/workbench/home/components/panel'
import { fetchPerformance } from '#/features/workbench/home/lib/performance-api'
import type { HomeT } from '#/features/workbench/home/types/dashboard'
import { Button } from '#/shared/ui/button'

const TOKEN_KEYS = [
  'input_tokens',
  'output_tokens',
  'cached_tokens',
  'reasoning_tokens',
] as const

function TokenFacts({ summary, t }: { summary: PerformanceSummary; t: HomeT }) {
  return (
    <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
      {TOKEN_KEYS.map((key) => (
        <div key={key}>
          <dt className="text-muted-foreground">{t(`performance.${key}`)}</dt>
          <dd>
            {t('performance.tokenValue', {
              value: summary.token_totals[key].known_tokens,
              unknown: summary.token_totals[key].unknown_events,
            })}
          </dd>
          <dd className="text-xs text-muted-foreground">
            {t('performance.qualityValue', {
              actual: summary.reported_token_totals[key],
              estimated: summary.estimated_token_totals[key],
              unverified: summary.unverified_token_totals[key],
            })}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function Details({
  workflow,
  range,
  boundary,
  scope,
  filters,
  t,
}: {
  workflow: PerformanceWorkflow
  range: { start: string; end: string }
  boundary: string
  scope: unknown
  filters: { model?: string; status?: string }
  t: HomeT
}) {
  const [cursor, setCursor] = useState<string | undefined>()
  const detail = useQuery({
    queryKey: [
      'console-performance-detail',
      scope,
      range,
      filters,
      boundary,
      workflow.user_id,
      workflow.workflow_id,
      cursor,
    ],
    queryFn: ({ signal }) =>
      fetchPerformance<PerformanceObservation>(
        {
          ...range,
          ...filters,
          view: 'details',
          user_id: workflow.user_id,
          workflow_id: workflow.workflow_id,
          observed_before: boundary,
          cursor,
          page_size: 20,
        },
        signal,
      ),
  })
  if (detail.isPending) return <p role="status">{t('performance.loading')}</p>
  if (detail.isError) return <EmptyState>{t('requestFailed')}</EmptyState>
  return (
    <div className="mt-4 space-y-3 border-t border-border pt-4">
      <h3 className="font-medium">{workflow.workflow_id}</h3>
      {detail.data.summary && (
        <TokenFacts summary={detail.data.summary} t={t} />
      )}
      <p className="text-xs text-muted-foreground">
        {t('performance.durationNote')}
      </p>
      {detail.data.summary &&
        Object.entries(detail.data.summary.duration_by_kind).map(
          ([kind, value]) => (
            <p className="text-xs text-muted-foreground" key={kind}>
              {t('performance.spanValue', {
                kind,
                value: value.observed_span_seconds.toFixed(3),
                unknown: value.unknown_duration_observations,
              })}
            </p>
          ),
        )}
      <div className="overflow-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              <th>{t('performance.stage')}</th>
              <th>{t('performance.status')}</th>
              <th>{t('performance.model')}</th>
              <th>{t('performance.seconds')}</th>
            </tr>
          </thead>
          <tbody>
            {detail.data.items?.map((row) => (
              <tr key={`${row.event_name}:${row.event_id}`}>
                <td className="py-2">{row.stage}</td>
                <td>{row.status}</td>
                <td>{row.model}</td>
                <td>
                  {row.duration_seconds == null
                    ? t('performance.unknown')
                    : row.duration_seconds.toFixed(3)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detail.data.next_cursor && (
        <Button
          variant="outline"
          onClick={() => setCursor(detail.data.next_cursor ?? undefined)}
        >
          {t('performance.next')}
        </Button>
      )}
    </div>
  )
}

export function PerformancePanel({
  enabled,
  scope,
  t,
}: {
  enabled: boolean
  scope: unknown
  t: HomeT
}) {
  const [range, setRange] = useState(() => ({
    start: new Date(Date.now() - 24 * 3600_000).toISOString(),
    end: new Date().toISOString(),
  }))
  const [hours, setHours] = useState(24)
  const [model, setModel] = useState('')
  const [status, setStatus] = useState('')
  const [filters, setFilters] = useState<{ model?: string; status?: string }>(
    {},
  )
  const [cursor, setCursor] = useState<string | undefined>()
  const [selected, setSelected] = useState<PerformanceWorkflow | null>(null)
  const query = useQuery({
    enabled,
    queryKey: ['console-performance', scope, range, filters, cursor],
    queryFn: ({ signal }) =>
      fetchPerformance<PerformanceWorkflow>(
        { ...range, ...filters, view: 'workflows', page_size: 20, cursor },
        signal,
      ),
    retry: false,
  })
  const data = query.data
  return (
    <Panel>
      <SectionHeading
        title={t('performance.title')}
        description={t('performance.description')}
        action={
          <Button
            variant="outline"
            disabled={!enabled}
            onClick={() => {
              setRange({
                start: new Date(Date.now() - hours * 3600_000).toISOString(),
                end: new Date().toISOString(),
              })
              setCursor(undefined)
              setSelected(null)
            }}
          >
            {t('performance.refresh')}
          </Button>
        }
      />
      <form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          setFilters({
            model: model.trim() || undefined,
            status: status || undefined,
          })
          setRange({
            start: new Date(Date.now() - hours * 3600_000).toISOString(),
            end: new Date().toISOString(),
          })
          setCursor(undefined)
          setSelected(null)
        }}
      >
        <label className="text-sm">
          {t('performance.range')}
          <select
            className="ml-2 rounded border border-border bg-background p-2"
            value={hours}
            onChange={(event) => setHours(Number(event.target.value))}
          >
            {[1, 6, 24].map((value) => (
              <option key={value} value={value}>
                {t('performance.hours', { count: value })}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          {t('performance.model')}
          <input
            className="ml-2 rounded border border-border bg-background p-2"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          />
        </label>
        <label className="text-sm">
          {t('performance.status')}
          <select
            className="ml-2 rounded border border-border bg-background p-2"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">{t('performance.all')}</option>
            <option value="success">{t('performance.success')}</option>
            <option value="failed">{t('performance.failed')}</option>
            <option value="cancelled">{t('performance.cancelled')}</option>
          </select>
        </label>
        <Button type="submit" variant="outline" disabled={!enabled}>
          {t('performance.apply')}
        </Button>
      </form>
      {!enabled ? (
        <EmptyState>{t('usageAccessRequired')}</EmptyState>
      ) : query.isPending ? (
        <p role="status">{t('performance.loading')}</p>
      ) : query.isError ? (
        <EmptyState>{t('requestFailed')}</EmptyState>
      ) : !data?.enabled ? (
        <EmptyState>{t('performance.disabled')}</EmptyState>
      ) : (
        <div className="space-y-4">
          <p role="note" className="text-sm text-muted-foreground">
            {t('performance.coverage')}
          </p>
          {data.coverage === 'retention_limited' && (
            <p role="alert">{t('performance.retentionLimited')}</p>
          )}
          {data.coverage === 'truncated' && (
            <p role="alert">{t('performance.truncated')}</p>
          )}
          {data.summary && (
            <>
              <TokenFacts summary={data.summary} t={t} />
              <p className="text-sm">
                {t('performance.cacheCoverage', {
                  value:
                    data.summary.cache_hit_rate == null
                      ? t('performance.unknown')
                      : `${(data.summary.cache_hit_rate * 100).toFixed(1)}%`,
                  count: data.summary.cache_covered_events,
                  unverified: data.summary.unverified_usage_events,
                  unknown: data.summary.unknown_usage_events,
                  incomplete: data.summary.incomplete_usage_events,
                })}
              </p>
            </>
          )}
          {data.collection?.producers.map((producer) => (
            <p
              className="text-xs text-muted-foreground"
              key={`${producer.producer_id}:${producer.user_id}`}
            >
              {t('performance.collector', {
                user: producer.user_id,
                accepted: producer.accepted,
                persisted: producer.persisted,
                dropped: producer.dropped,
                failed: producer.failed,
                pending: producer.pending,
                uncertain: producer.uncertain,
              })}
            </p>
          ))}
          {data.items?.length ? (
            <ul className="divide-y divide-border">
              {data.items.map((workflow) => (
                <li key={`${workflow.user_id}:${workflow.workflow_id}`}>
                  <button
                    type="button"
                    className="w-full py-3 text-left text-sm underline underline-offset-4"
                    onClick={() => setSelected(workflow)}
                  >
                    {workflow.workflow_id}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>{t('performance.empty')}</EmptyState>
          )}
          {data.next_cursor && (
            <Button
              variant="outline"
              onClick={() => {
                setCursor(data.next_cursor ?? undefined)
                setSelected(null)
              }}
            >
              {t('performance.next')}
            </Button>
          )}
          {selected && data.observed_before && (
            <Details
              key={`${JSON.stringify(scope)}:${selected.user_id}:${selected.workflow_id}:${data.observed_before}`}
              workflow={selected}
              range={range}
              boundary={data.observed_before}
              scope={scope}
              filters={filters}
              t={t}
            />
          )}
        </div>
      )}
    </Panel>
  )
}
