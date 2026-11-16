import type { DashboardStatus } from '../../shared/dashboard/dashboard-state.js';
import type { Locale } from '../../shared/locale.js';
import { ExperienceMetrics } from './experience-metrics.js';
import { ExperienceQueryState } from './experience-state.js';
import { ExperienceTimeRange } from './experience-time-range.js';
import { ExperienceTrajectories } from './experience-trajectories.js';
import type {
  ExperienceDashboardData,
  ExperienceDashboardLabels,
  ExperienceOutcome,
} from './types.js';

/** 结果分布固定按火山版的顺序显示，服务端返回的顺序不影响。 The fixed order. */
const ORDER: readonly ExperienceOutcome[] = [
  'success',
  'partial',
  'unfinished',
  'failure',
  'unknown',
];

/**
 * 「应用效果」页签：标题与时间范围、三个指标、任务结果分布、注入轨迹。
 *
 * The "application impact" tab: the heading with its time range, three metrics, the
 * outcome distribution and the injection trajectories.
 */
export function ExperienceImpact({
  data,
  labels: t,
  locale,
  days,
  onDaysChange,
  effectState = {},
  outcomesState,
  trajectoriesState,
  hasMore,
  loadingMore,
  onLoadMore,
  onOpenUri,
}: {
  data: ExperienceDashboardData;
  labels: ExperienceDashboardLabels;
  locale: Locale;
  days: number;
  onDaysChange: (days: number) => void;
  effectState?: DashboardStatus;
  outcomesState: DashboardStatus;
  trajectoriesState: DashboardStatus;
  hasMore: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  onOpenUri?: (uri: string) => void;
}) {
  const total = data.outcomes?.reduce((sum, item) => sum + item.count, 0) ?? 0;
  return (
    <div className="ov-experience-dashboard__impact">
      <div className="ov-experience-dashboard__heading">
        <div className="ov-experience-dashboard__heading-title">
          <h2>{t.application}</h2>
          <span className="ov-experience-dashboard__notice">{t.notice}</span>
        </div>
        <ExperienceTimeRange days={days} labels={t} onChange={onDaysChange} />
      </div>
      <ExperienceMetrics
        data={data}
        labels={t}
        locale={locale}
        state={effectState}
      />
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
                <div className="ov-experience-dashboard__outcome-meta">
                  <span>{t[outcome]}</span>
                  <strong>{count}</strong>
                </div>
                <div
                  className="ov-experience-dashboard__outcome-track"
                  aria-hidden="true"
                >
                  <span
                    style={{
                      width: `${
                        total > 0
                          ? Math.max((count / total) * 100, count ? 2 : 0)
                          : 0
                      }%`,
                    }}
                  />
                </div>
              </div>
            );
          })
        ) : (
          <ExperienceQueryState
            state={outcomesState}
            rows={3}
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
          <>
            <ExperienceTrajectories
              items={data.trajectories}
              labels={t}
              locale={locale}
              hasMore={hasMore}
              loadingMore={loadingMore ?? trajectoriesState.loading}
              scrollLoadMore
              onLoadMore={onLoadMore}
              onOpenUri={onOpenUri}
            />
            {/* 追加下一页失败时，已加载的轨迹继续显示，错误与重试就地放在列表下方，
                而不是把整条时间线换成错误态。
             *
             * When appending a page fails, the loaded trajectories stay and the error
             * with its retry sits below the list instead of replacing the timeline. */}
            {trajectoriesState.error ? (
              <ExperienceQueryState
                state={trajectoriesState}
                rows={3}
                empty={t.emptyTimeline}
                loading={t.loading}
                failed={t.failed}
                retry={t.retry}
              />
            ) : null}
          </>
        ) : (
          <ExperienceQueryState
            state={trajectoriesState}
            rows={3}
            empty={t.emptyTimeline}
            loading={t.loading}
            failed={t.failed}
            retry={t.retry}
          />
        )}
      </section>
    </div>
  );
}
