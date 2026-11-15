import { ExperienceTrajectories } from './experience-trajectories.js';
import type {
  ExperienceDashboardLabels,
  ExperienceTrajectory,
} from './types.js';

/**
 * 「来源追溯」页签：按生成该经验的提交分组，每组默认展开，组内是该次提交涉及的
 * 轨迹时间线。
 *
 * 火山版的分组来自提交记录，这里的契约只给出一组扁平数据（`sources`），因此只
 * 保留一组，折叠交互与视觉规则与火山版一致。
 *
 * The "source trace" tab groups the source trajectories by the commit that produced
 * the experience, each group expanded by default.
 *
 * The console's groups come from commit records; this component's contract supplies
 * one flat list (`sources`), so a single group remains, with the console's
 * disclosure and styling.
 */
export function ExperienceSources({
  items,
  total,
  labels: t,
  locale = 'en-US',
  onOpenUri,
}: {
  items: readonly ExperienceTrajectory[];
  total?: number;
  labels: ExperienceDashboardLabels;
  locale?: 'en-US' | 'zh-CN';
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <details className="ov-experience-dashboard__source-group" open>
      <summary>
        <span className="ov-experience-dashboard__source-title">
          <strong>{t.latest}</strong>
          <ChevronToggle />
        </span>
        <span className="ov-experience-dashboard__source-count">
          {t.sourceCount.replace('{count}', String(total ?? items.length))}
        </span>
      </summary>
      <ExperienceTrajectories
        items={items}
        labels={t}
        locale={locale}
        onOpenUri={onOpenUri}
      />
    </details>
  );
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
