import { ExperienceQueryState } from './experience-state.js';
import { experienceTime } from './experience-time.js';
import { ExperienceTrajectories } from './experience-trajectories.js';
import type {
  ExperienceDashboardLabels,
  ExperienceDashboardState,
  ExperienceSourceGroup,
  ExperienceTrajectory,
} from './types.js';

export function ExperienceSources({
  items = [],
  groups,
  state,
  labels: t,
  locale = 'en-US',
  onOpenUri,
  onOpenTrajectory,
}: {
  items?: readonly ExperienceTrajectory[];
  groups?: readonly ExperienceSourceGroup[];
  state?: ExperienceDashboardState;
  labels: ExperienceDashboardLabels;
  locale?: 'en-US' | 'zh-CN';
  onOpenUri?: (uri: string) => void;
  onOpenTrajectory?: (
    trajectory: ExperienceTrajectory,
    trigger: HTMLButtonElement,
  ) => void;
}) {
  if ((groups?.length ?? items.length) === 0)
    return (
      <ExperienceQueryState
        state={state}
        rows={4}
        empty={t.emptySource}
        loading={t.loading}
        failed={t.failed}
        retry={t.retry}
      />
    );
  const entries = groups ?? [
    { id: 'latest', updatedAt: '', trajectories: items },
  ];
  return entries.map(({ id, updatedAt, trajectories }, index) => (
    <details key={id} className="ov-experience-dashboard__source-group" open>
      <summary>
        <span className="ov-experience-dashboard__source-title">
          <strong>
            {index === 0 ? (
              t.latest
            ) : (
              <>
                {t.updated}{' '}
                <time dateTime={updatedAt}>
                  {experienceTime(updatedAt, locale)}
                </time>
              </>
            )}
          </strong>
          <ChevronToggle />
        </span>
        <span className="ov-experience-dashboard__source-count">
          {t.sourceCount.replace('{count}', String(trajectories.length))}
        </span>
      </summary>
      <ExperienceTrajectories
        items={trajectories}
        labels={t}
        locale={locale}
        onOpenUri={onOpenUri}
        onOpenTrajectory={onOpenTrajectory}
      />
    </details>
  ));
}

/**
 * 折叠指示器。`<details>` 自己维护展开状态，图标只跟随 `open` 旋转。
 *
 * The disclosure indicator. `<details>` owns the expanded state; the glyph only
 * rotates with `open`.
 */
function ChevronToggle() {
  return (
    <svg
      aria-hidden="true"
      width={16}
      height={16}
      viewBox="0 0 14 14"
      fill="none"
      className="ov-experience-dashboard__source-chevron"
    >
      <path
        d="M9.60734 5.23234C9.77819 5.06149 10.0551 5.06149 10.226 5.23234C10.3968 5.40319 10.3968 5.68014 10.226 5.85099L7.30933 8.76766C7.13847 8.93851 6.86153 8.93851 6.69067 8.76766L3.77401 5.85099C3.60315 5.68014 3.60315 5.40319 3.77401 5.23234C3.94486 5.06149 4.22181 5.06149 4.39266 5.23234L7 7.83968L9.60734 5.23234Z"
        fill="currentColor"
      />
    </svg>
  );
}
