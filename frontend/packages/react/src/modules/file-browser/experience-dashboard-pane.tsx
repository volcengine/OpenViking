import {
  ExperienceDashboard,
  type ExperienceDashboardProps,
} from '../../components/experience-dashboard/index.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import type { OvRequestOptions } from '../../client/types.js';
import { useExperienceDashboard } from './use-experience-dashboard.js';
import { useExperienceTrajectories } from './use-experience-trajectories.js';
import {
  EXPERIENCE_PAGE_SIZE,
  isExperienceDirectory,
} from './experience-data.js';

type Props = Pick<ExperienceDashboardProps, 'uri' | 'locale' | 'onOpenUri'> & {
  requestOptions?: OvRequestOptions;
  dataSource?: FileBrowserExperienceDataSource;
  refreshGeneration?: number;
};
export function ExperienceDashboardPane({
  uri,
  locale,
  dataSource,
  requestOptions,
  refreshGeneration,
  onOpenUri,
}: Props) {
  const browser = useExperienceDashboard({
    uri,
    dataSource,
    requestOptions,
    refreshGeneration,
  });
  const directory = isExperienceDirectory(uri);
  // 轨迹自己管分页：目录视图不需要它，只有文件视图的「应用效果」页签会取数。
  //
  // Trajectories own their paging: the directory view never needs it, and only the
  // file view's Impact tab fetches.
  const trajectories = useExperienceTrajectories({
    uri,
    days: browser.days,
    enabled: !directory && browser.tab === 'impact',
    dataSource,
    requestOptions,
    refreshGeneration,
  });
  const status = (query: {
    loading: boolean;
    error: Error | null;
    retry: () => void;
  }) => ({ loading: query.loading, error: query.error, onRetry: query.retry });
  return (
    <ExperienceDashboard
      uri={uri}
      kind={directory ? 'directory' : 'file'}
      locale={locale}
      onOpenUri={onOpenUri}
      data={{
        experiences: browser.list.data?.items,
        total: browser.list.data?.total,
        outcomes: browser.outcomes.data,
        trajectories: trajectories.data?.items,
        trajectoryTotal: trajectories.data?.total,
        sources: browser.sources.data,
      }}
      tab={browser.tab}
      days={browser.days}
      page={browser.page}
      pageSize={EXPERIENCE_PAGE_SIZE}
      startIndex={browser.page * EXPERIENCE_PAGE_SIZE}
      hasMore={Boolean(
        directory ? browser.list.data?.hasMore : trajectories.hasMore,
      )}
      loadingMore={trajectories.loadingMore}
      states={{
        list: status(browser.list),
        sources: status(browser.sources),
        outcomes: status(browser.outcomes),
        trajectories: {
          loading: trajectories.loading,
          error: trajectories.error,
          onRetry: trajectories.retry,
        },
      }}
      onTabChange={browser.setTab}
      onDaysChange={browser.setDays}
      onPageChange={browser.setPage}
      onLoadMore={trajectories.loadMore}
    />
  );
}
