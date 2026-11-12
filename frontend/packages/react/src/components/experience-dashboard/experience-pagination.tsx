import { Button } from '../../shared/primitives/button/button.js';
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
      <Button
        size="small"
        type="default"
        htmlType="button"
        disabled={!page || loading || !onPageChange}
        onClick={() => onPageChange?.(page - 1)}
      >
        {t.previous}
      </Button>
      <span>{page + 1}</span>
      <Button
        size="small"
        type="default"
        htmlType="button"
        disabled={!hasMore || loading || !onPageChange}
        onClick={() => onPageChange?.(page + 1)}
      >
        {t.next}
      </Button>
    </div>
  );
}
