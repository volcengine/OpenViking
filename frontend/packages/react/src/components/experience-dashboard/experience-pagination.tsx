import { Pagination } from '../../shared/primitives/pagination/pagination.js';
import type { ExperienceDashboardLabels } from './types.js';

/**
 * 看板的分页：把宿主给的「当前页 + 是否还有下一页」翻译成页码数，再交给分页器。
 *
 * 接口按游标分页，不返回总数；`total` 与 `pageSize` 都给出时才算页数，否则以
 * 「还有下一页」为界——当前页之后至少还有一页。
 *
 * The board's pager: it turns the host's "current page plus whether more exist" into
 * a page count for the pager.
 *
 * The endpoints page by cursor and report no total; the count is derived from
 * `total`/`pageSize` when both are given, and otherwise bounded by "there is a next
 * page" — at least one page follows the current one.
 */
export function ExperiencePagination({
  page,
  pageCount,
  total,
  pageSize,
  loading,
  labels: t,
  onPageChange,
}: {
  page: number;
  pageCount?: number;
  total?: number;
  pageSize: number;
  loading?: boolean;
  labels: ExperienceDashboardLabels;
  onPageChange?: (page: number) => void;
}) {
  const count =
    total === undefined
      ? (pageCount ?? 1)
      : Math.max(1, Math.ceil(total / pageSize));
  if (count <= 1) return null;
  return (
    <div className="ov-experience-dashboard__pagination">
      <Pagination
        current={page + 1}
        pageCount={count}
        disabled={loading || !onPageChange}
        labels={{ previous: t.previous, next: t.next }}
        onChange={(next) => onPageChange?.(next - 1)}
      />
    </div>
  );
}
