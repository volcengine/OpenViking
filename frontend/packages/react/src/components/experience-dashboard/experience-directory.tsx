import type { ReactNode } from 'react';
import { DashboardState } from '../../shared/dashboard/dashboard-state.js';
import type { Locale } from '../../shared/locale.js';
import { ExperienceList } from './experience-list.js';
import { ExperienceTableSkeleton } from './experience-state.js';
import type {
  ExperienceDashboardData,
  ExperienceDashboardLabels,
  ExperienceDashboardState,
} from './types.js';

export function ExperienceDirectory({
  data,
  labels,
  locale,
  page,
  pageSize,
  startIndex,
  state,
  onOpenUri,
  renderList,
  pagination,
}: {
  data: ExperienceDashboardData;
  labels: ExperienceDashboardLabels;
  locale: Locale;
  page: number;
  pageSize: number;
  startIndex: number;
  state?: ExperienceDashboardState;
  onOpenUri?: (uri: string) => void;
  renderList: (content: ReactNode) => ReactNode;
  pagination: ReactNode;
}) {
  // Match the last page's size so its skeleton does not shift the layout.
  const remainingRows = Math.max(
    0,
    (data.total ?? (page + 1) * pageSize) - page * pageSize,
  );
  const content = data.experiences?.length ? (
    <ExperienceList
      items={data.experiences}
      locale={locale}
      startIndex={startIndex}
      labels={labels}
      onOpenUri={onOpenUri}
    />
  ) : state?.loading ? (
    <ExperienceTableSkeleton
      rows={Math.min(pageSize, remainingRows)}
      label={labels.loading}
    />
  ) : (
    <DashboardState
      state={state}
      empty={labels.empty}
      loading={labels.loading}
      failed={labels.failed}
      retry={labels.retry}
    />
  );
  return (
    <div className="ov-experience-dashboard__body">
      <h2 className="ov-experience-dashboard__list-title">
        {labels.allExperiences}
        {data.total === undefined ? '' : ` · ${data.total}`}
      </h2>
      {renderList(content)}
      {pagination}
    </div>
  );
}
