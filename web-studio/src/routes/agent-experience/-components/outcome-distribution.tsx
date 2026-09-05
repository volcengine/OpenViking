import { useTranslation } from 'react-i18next'

import { OUTCOME_COLORS, TRAJECTORY_OUTCOMES } from '../-lib/experience'
import type { OutcomeCount } from '../-lib/types'

function OutcomeLegend({
  count,
  label,
  outcome,
}: {
  count: number
  label: string
  outcome: OutcomeCount['outcome']
}) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <span
        aria-hidden="true"
        className={`size-2 shrink-0 rounded-full ${OUTCOME_COLORS[outcome]}`}
      />
      <span className="truncate text-muted-foreground">{label}</span>
      <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-foreground">
        {count}
      </span>
    </div>
  )
}

/**
 * Horizontal stacked bar plus legend for the five fixed trajectory outcomes.
 *
 * Zero-count buckets are still rendered (muted) so the legend stays stable and
 * users can discover which outcomes exist.
 */
export function OutcomeDistribution({
  distribution,
}: {
  distribution: OutcomeCount[]
}) {
  const { t } = useTranslation('agentExperiencePage')
  const byOutcome = new Map(distribution.map((item) => [item.outcome, item]))
  const total = distribution.reduce((sum, item) => sum + item.count, 0)
  const successCount = byOutcome.get('success')?.count ?? 0

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-2xl font-semibold tabular-nums">{total}</span>
        <span className="text-sm text-muted-foreground">
          {t('detail.outcomeTotal', { count: total })}
        </span>
        {total > 0 ? (
          <span className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
            {t('detail.successRate', {
              rate: Math.round((successCount / total) * 100),
            })}
          </span>
        ) : null}
      </div>

      {total > 0 ? (
        <div
          aria-hidden="true"
          className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted"
        >
          {distribution.map((item) =>
            item.count > 0 ? (
              <div
                key={item.outcome}
                className={OUTCOME_COLORS[item.outcome]}
                style={{ width: `${(item.count / total) * 100}%` }}
              />
            ) : null,
          )}
        </div>
      ) : null}

      <div className="grid gap-1.5 text-sm sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
        {TRAJECTORY_OUTCOMES.map((outcome) => (
          <OutcomeLegend
            key={outcome}
            count={byOutcome.get(outcome)?.count ?? 0}
            label={t(`outcomes.${outcome}`)}
            outcome={outcome}
          />
        ))}
      </div>
    </div>
  )
}
