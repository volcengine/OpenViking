import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { getSchedules } from '../-api'

export function Schedules({
  canManage,
  scope,
}: {
  canManage: boolean
  scope: string
}) {
  const { t, i18n } = useTranslation('vikingbot')
  const query = useQuery({
    queryKey: ['vikingbot', scope, 'schedules'],
    queryFn: getSchedules,
    enabled: canManage,
    refetchInterval: 15000,
    retry: false,
  })
  const date = (value: number | null) =>
    value == null ? '—' : new Date(value).toLocaleString(i18n.language)
  if (!canManage) return <p className="p-6">{t('schedule.adminOnly')}</p>
  return (
    <section className="min-h-0 flex-1 space-y-5 overflow-auto p-4 md:p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <h2 className="font-semibold">{t('schedules')}</h2>
          <p className="text-sm text-muted-foreground">{t('schedule.scope')}</p>
          {query.data && (
            <p className="text-sm">
              {t(query.data.running ? 'schedule.running' : 'schedule.stopped')}
            </p>
          )}
        </div>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          {t('schedule.refresh')}
        </Button>
      </div>
      {query.isPending ? (
        <p role="status">{t('loading')}</p>
      ) : query.error ? (
        <p role="alert" className="text-destructive">
          {query.error.message}
        </p>
      ) : !query.data.available ? (
        <p>{t('schedule.unavailable')}</p>
      ) : !query.data.jobs.length ? (
        <div className="rounded-lg border p-6 space-y-3">
          <h3 className="font-medium">{t('schedule.empty')}</h3>
          <p className="text-sm text-muted-foreground">{t('schedule.guide')}</p>
        </div>
      ) : (
        query.data.jobs.map((job) => (
          <article key={job.id} className="space-y-4 rounded-lg border p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="min-w-0 break-words font-medium">{job.name}</h3>
              <span className="text-sm text-muted-foreground">
                {t(job.enabled ? 'schedule.enabled' : 'schedule.disabled')}
              </span>
            </div>
            <p className="whitespace-pre-wrap break-words text-sm">
              {job.message}
            </p>
            <dl className="grid gap-4 text-sm sm:grid-cols-2 xl:grid-cols-4">
              <div>
                <dt className="text-muted-foreground">{t('schedule.rule')}</dt>
                <dd className="break-words">
                  {job.schedule.kind === 'at'
                    ? t('schedule.once', { time: date(job.schedule.at_ms) })
                    : job.schedule.kind === 'every'
                      ? t('schedule.every', {
                          seconds: (job.schedule.every_ms ?? 0) / 1000,
                        })
                      : `${job.schedule.expr} · ${job.schedule.tz || t('schedule.serverTimezone')}`}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t('schedule.next')}</dt>
                <dd>{date(job.state.next_run_at_ms)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t('schedule.last')}</dt>
                <dd>{date(job.state.last_run_at_ms)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  {t('schedule.result')}
                </dt>
                <dd>{t(`schedule.${job.state.last_status || 'pending'}`)}</dd>
              </div>
            </dl>
            <p className="text-xs text-muted-foreground">
              {t(job.deliver ? 'schedule.deliver' : 'schedule.noDeliver')}
            </p>
          </article>
        ))
      )}
      <p className="text-xs text-muted-foreground">{t('schedule.note')}</p>
    </section>
  )
}
