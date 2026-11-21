/**
 * 换层级时正文的过渡参数与判定。抽成纯函数与常量，组件只负责按阶段渲染；不
 * 引用组件或底座，因此可以直接在测试里驱动。
 *
 * 两段时间：
 *
 * - 宽限期：旧内容仍可见。新内容在这段时间内就绪就无缝替换，用户看不到空白。
 * - 最短停留：宽限期用尽后加载态露出，并至少停留这么久。否则内容紧随阈值就绪
 *   时，加载态会一闪而过——只是把闪动挪了个位置。
 *
 * Two durations: a grace period during which the previous content stays visible, and
 * a minimum time a loading state stays once shown. Without the second, content ready
 * just past the threshold makes the loading state blink — the same flaw, moved.
 */
export const LEVEL_TRANSITION_GRACE_MS = 150;
export const LEVEL_TRANSITION_MIN_VISIBLE_MS = 250;

/** 后台那份内容的状态。 The status of the background copy. */
export type ProbeStatus = 'pending' | 'ready' | 'failed';

/** 过渡阶段。 The phase of the transition. */
export type TransitionPhase = 'idle' | 'grace' | 'loading';

/**
 * 换层级时正文的过渡参数与判定。抽成纯函数与常量，组件只负责按阶段渲染；不
 * 引用组件或底座，因此可以直接在测试里驱动。
 *
 * 媒体判定与「要不要异步加载」在 `media-kind.ts`，与正文共用同一条规则。
 *
 * The transition's parameters and decisions, as pure functions and constants so the
 * component only renders by phase and the tests can drive them directly.
 *
 * Media classification and "does this need async loading" live in `media-kind.ts`,
 * sharing one rule with the body.
 */

/**
 * 把底座的加载状态折算成过渡层关心的三档。只把 `ready` 当作就绪：`idle` 表示
 * 还没开始，其余中间态都还在路上；`unsupported` 与 `error` 是终态，同样要换上去
 * 让用户看到兜底提示。
 *
 * Folds the base's load state into the three the transition cares about. Only `ready`
 * counts as ready; `idle` has not started and the other intermediate states are still
 * on their way. `unsupported` and `error` are terminal, so they swap in too, letting
 * the user see the fallback.
 */
export function probeStatusOf(state: string): ProbeStatus {
  if (state === 'ready') return 'ready';
  if (state === 'unsupported' || state === 'error') return 'failed';
  return 'pending';
}

/**
 * 该不该把后台那一份换上屏幕。
 *
 * 失败立即换，不让兜底提示被加载遮罩挡住；宽限期内就绪立即换；已经在显示加载态
 * 时，要等它停满最短时间。
 *
 * Whether the background copy should replace what is on screen: a failure swaps at
 * once so its message is not hidden behind the overlay, a ready copy swaps at once
 * inside the grace period, and once the loading state is up it waits out the minimum.
 *
 * @param loadingAt 加载态露出的时刻；`null` 表示还没露出。
 */
export function shouldCommitProbe(
  phase: TransitionPhase,
  status: ProbeStatus,
  loadingAt: number | null,
  now: number,
  minVisibleMs = LEVEL_TRANSITION_MIN_VISIBLE_MS,
  /** 这一层是否仍在显示自己的加载态。 Whether the layer still shows its own loading. */
  stillLoading = false,
): { commit: boolean; waitMs: number } {
  if (status === 'pending') return { commit: false, waitMs: 0 };
  // 渲染器自己还在加载：即使外壳已经 `ready`，换上去也会把它的遮罩露出来。实测
  // 外壳就绪后遮罩还会再停约两帧（Markdown 解析、图片解码）。
  //
  // The renderer itself is still loading: swapping now would put its overlay on screen
  // even though the shell is `ready`. In practice it lingers about two frames.
  if (stillLoading) return { commit: false, waitMs: 0 };
  if (status === 'failed' || phase === 'grace')
    return { commit: true, waitMs: 0 };
  const shownAt = loadingAt ?? now;
  const remaining = shownAt + minVisibleMs - now;
  return remaining <= 0
    ? { commit: true, waitMs: 0 }
    : { commit: false, waitMs: remaining };
}
