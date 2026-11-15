import { DashboardState } from '../../shared/dashboard/dashboard-state.js';
import type { DashboardStatus } from '../../shared/dashboard/dashboard-state.js';

/**
 * 加载骨架：几条等高的占位条，随主题呼吸。
 *
 * 数据到达前先占住最终的高度，页面不会先塌成一行再撑开。视觉规则对齐控制台：
 * 16 高、2 圆角、1.4 秒的明暗循环。占位条本身对读屏器隐藏，加载中的文字另用
 * 视觉隐藏的一行给出，状态仍然通过文本表达。
 *
 * The loading skeleton: same-height placeholder bars that pulse with the theme.
 *
 * It holds the final height before data arrives, so the page does not collapse to one
 * line and expand again. The look follows the console: 16 high, 2 radius, a 1.4-second
 * shimmer. The bars are hidden from assistive technology; a visually hidden line
 * carries the loading text instead, so the state is still expressed as text.
 */
export function ExperienceSkeleton({
  rows = 3,
  label,
}: {
  rows?: number;
  label: string;
}) {
  return (
    <div className="ov-experience-dashboard__skeleton" aria-busy="true">
      <span className="ov-experience-dashboard__sr-only" role="status">
        {label}
      </span>
      <div aria-hidden="true">
        {Array.from({ length: rows }, (_, index) => (
          <span key={index} />
        ))}
      </div>
    </div>
  );
}

/**
 * 目录表的加载骨架：按真实表格的几何占位，表头 40 高、每行 82 高。
 *
 * 通用占位条撑不出表格的高度——四根 16 高的条只有 148px，而一页 20 行的表有
 * 1702px，数据到达时整页会突然长高一大截，下面的内容被推走。这里照着表的行列
 * 骨架画：一列序号、一列文件名、两列指标，行高与真表一致。
 *
 * The directory table's loading skeleton, holding the real table's geometry: a 40-high
 * header and 82-high rows.
 *
 * Generic placeholder bars cannot hold a table's height — four 16-high bars are 148px
 * against 1702px for a 20-row page — so the page jumps when the data lands and pushes
 * everything below it down. This draws the table's own shape instead: the index, the
 * file name and the two metrics, at the real row height.
 */
export function ExperienceTableSkeleton({
  rows,
  label,
}: {
  rows: number;
  label: string;
}) {
  return (
    <div className="ov-experience-dashboard__table-skeleton" aria-busy="true">
      <span className="ov-experience-dashboard__sr-only" role="status">
        {label}
      </span>
      <div aria-hidden="true">
        <div className="ov-experience-dashboard__table-skeleton-head" />
        {Array.from({ length: rows }, (_, index) => (
          <div
            key={index}
            className="ov-experience-dashboard__table-skeleton-row"
          >
            <span>
              <i />
            </span>
            <span>
              <i />
              <i />
            </span>
            <span>
              <i />
            </span>
            <span>
              <i />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * 一个区域的状态：加载中给骨架，其余情况交给共享的空态与错误态。
 *
 * 把「加载中显示什么」收在一处，各区域只声明占位条数量，避免六处重复同一个
 * 三元判断。
 *
 * The state of one section: a skeleton while loading, otherwise the shared empty and
 * error states.
 *
 * Keeping "what loading looks like" in one place lets each section declare only its row
 * count instead of repeating the same ternary six times.
 */
export function ExperienceQueryState({
  state = {},
  rows = 3,
  empty,
  loading,
  failed,
  retry,
}: {
  state?: DashboardStatus;
  rows?: number;
  empty: string;
  loading: string;
  failed: string;
  retry: string;
}) {
  if (state.loading) return <ExperienceSkeleton rows={rows} label={loading} />;
  return (
    <DashboardState
      state={state}
      empty={empty}
      loading={loading}
      failed={failed}
      retry={retry}
    />
  );
}
