import {
  FileInputIcon,
  ScanSearchIcon,
  ActivityIcon,
} from '../../icons/index.js';
import { DashboardState } from '../../shared/dashboard/dashboard-state.js';
import type { DashboardStatus } from '../../shared/dashboard/dashboard-state.js';
import { ExperienceTrajectories } from './experience-trajectories.js';
import type {
  ExperienceDashboardData,
  ExperienceDashboardLabels,
} from './types.js';

const ORDER = [
  'success',
  'partial',
  'unfinished',
  'failure',
  'unknown',
] as const;
export function ExperienceImpact({
  data,
  labels: t,
  days,
  onDaysChange,
  outcomesState,
  trajectoriesState,
  onOpenUri,
}: {
  data: ExperienceDashboardData;
  labels: ExperienceDashboardLabels;
  days: number;
  onDaysChange: (days: number) => void;
  outcomesState: DashboardStatus;
  trajectoriesState: DashboardStatus;
  onOpenUri?: (uri: string) => void;
}) {
  const total = data.outcomes?.reduce((sum, item) => sum + item.count, 0) ?? 0;
  const metrics = [
    {
      label: t.injection,
      value: data.effect?.injectionCount,
      icon: <FileInputIcon width={14} height={14} />,
    },
    {
      label: t.recall,
      value: data.effect?.recallCount,
      icon: <ScanSearchIcon width={14} height={14} />,
    },
    {
      label: t.rate,
      value: data.effect
        ? new Intl.NumberFormat('en-US', {
            style: 'percent',
            maximumFractionDigits: 1,
          }).format(data.effect.injectionRate)
        : undefined,
      icon: <ActivityIcon width={14} height={14} />,
    },
  ];
  return (
    <div className="ov-experience-dashboard__impact">
      <div className="ov-experience-dashboard__heading">
        <div>
          <h2>{t.application}</h2>
          <span>{t.notice}</span>
        </div>
        <select
          aria-label={t.range}
          value={days}
          onChange={(event) => onDaysChange(Number(event.target.value))}
        >
          <option value={7}>{t.week}</option>
          <option value={30}>{t.month}</option>
          <option value={0}>{t.all}</option>
        </select>
      </div>
      <dl className="ov-experience-dashboard__metrics">
        {metrics.map((metric) => (
          <div
            key={metric.label}
            title={metric.value === undefined ? t.unsupportedMetric : undefined}
          >
            {metric.icon}
            <dd>{metric.value ?? '—'}</dd>
            <dt>{metric.label}</dt>
          </div>
        ))}
      </dl>
      <section
        className="ov-experience-dashboard__distribution"
        aria-label={t.outcomes}
      >
        {data.outcomes?.length ? (
          ORDER.map((outcome) => {
            const count =
              data.outcomes?.find((item) => item.outcome === outcome)?.count ??
              0;
            return (
              <div className="ov-experience-dashboard__outcome" key={outcome}>
                <div>
                  <span>{t[outcome]}</span>
                  <strong>{count}</strong>
                </div>
                <div
                  className="ov-experience-dashboard__outcome-track"
                  aria-hidden="true"
                >
                  <span
                    style={{
                      width: `${total > 0 ? Math.max((count / total) * 100, count ? 2 : 0) : 0}%`,
                    }}
                  />
                </div>
              </div>
            );
          })
        ) : (
          <DashboardState
            state={outcomesState}
            empty={t.emptyOutcomes}
            loading={t.loading}
            failed={t.failed}
            retry={t.retry}
          />
        )}
      </section>
      <section className="ov-experience-dashboard__trajectories">
        <h3>
          {t.trajectories}
          {data.trajectoryTotal === undefined
            ? ''
            : ` · ${data.trajectoryTotal}`}
        </h3>
        {data.trajectories?.length ? (
          <ExperienceTrajectories
            items={data.trajectories}
            onOpenUri={onOpenUri}
          />
        ) : (
          <DashboardState
            state={trajectoriesState}
            empty={t.empty}
            loading={t.loading}
            failed={t.failed}
            retry={t.retry}
          />
        )}
      </section>
    </div>
  );
}
