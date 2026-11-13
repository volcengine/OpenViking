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
