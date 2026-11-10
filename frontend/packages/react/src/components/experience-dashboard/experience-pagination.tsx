import type { ExperienceDashboardLabels } from './types.js';
export function ExperiencePagination({
  page,
  hasMore,
  loading,
  labels: t,
  onPageChange,
}: {
  page: number;
  hasMore: boolean;
  loading?: boolean;
  labels: ExperienceDashboardLabels;
  onPageChange?: (page: number) => void;
}) {
  if (!page && !hasMore) return null;
  return (
    <div className="ov-experience-dashboard__pagination">
      <button
        type="button"
        disabled={!page || loading || !onPageChange}
        onClick={() => onPageChange?.(page - 1)}
      >
        {t.previous}
      </button>
      <span>{page + 1}</span>
      <button
        type="button"
        disabled={!hasMore || loading || !onPageChange}
        onClick={() => onPageChange?.(page + 1)}
      >
        {t.next}
      </button>
    </div>
  );
}
