import type { ExperienceTrajectory } from '../../components/experience-dashboard/types.js';
import type { ExperienceTrajectoryPage } from './experience-data.js';

/** 已加载的页与下一页的请求状态。 The loaded pages and the next page's request state. */
export interface TrajectoryPages {
  owner: unknown[];
  items: readonly ExperienceTrajectory[];
  total?: number;
  hasMore: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: Error | null;
  /** 已加载的页数；下一次「加载更多」的 offset 由它决定。 */
  /** How many pages are loaded; the next load-more offset comes from it. */
  loadedPages: number;
}

/** 空状态：还没加载任何页，或身份已经变化。 Empty: nothing loaded, or a new identity. */
export const emptyTrajectoryPages = (owner: unknown[]): TrajectoryPages => ({
  owner,
  items: [],
  hasMore: false,
  loading: false,
  loadingMore: false,
  error: null,
  loadedPages: 0,
});

/**
 * 开始一页请求：身份变了就丢弃旧页，否则保留已加载的记录只切换等待标记。
 *
 * Starts one page request: a new identity drops the old pages, while the same
 * identity keeps them and only flips the waiting flag.
 */
export function beginTrajectoryPage(
  previous: TrajectoryPages,
  owner: unknown[],
  mode: 'first' | 'more',
): TrajectoryPages {
  const same = previous.owner === owner;
  return {
    ...(same ? previous : emptyTrajectoryPages(owner)),
    owner,
    loading: mode === 'first',
    loadingMore: mode === 'more',
    error: null,
  };
}

/**
 * 一页成功返回：首页替换，后续页追加。
 *
 * A page came back: the first replaces, later ones append.
 */
export function appendTrajectoryPage(
  previous: TrajectoryPages,
  owner: unknown[],
  result: ExperienceTrajectoryPage,
  mode: 'first' | 'more',
): TrajectoryPages {
  const same = previous.owner === owner;
  return {
    owner,
    items:
      mode === 'first' || !same
        ? result.items
        : [...previous.items, ...result.items],
    total: result.total,
    hasMore: result.has_more,
    loading: false,
    loadingMore: false,
    error: null,
    loadedPages: mode === 'first' || !same ? 1 : previous.loadedPages + 1,
  };
}

/**
 * 一页失败：保留已加载的记录，只记录错误。
 *
 * A page failed: keep the loaded rows and only record the error.
 */
export function failTrajectoryPage(
  previous: TrajectoryPages,
  owner: unknown[],
  cause: unknown,
): TrajectoryPages {
  return {
    ...(previous.owner === owner ? previous : emptyTrajectoryPages(owner)),
    owner,
    loading: false,
    loadingMore: false,
    error: cause instanceof Error ? cause : new Error(String(cause)),
  };
}
