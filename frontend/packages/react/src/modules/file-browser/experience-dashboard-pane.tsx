import {
  ExperienceDashboard,
  type ExperienceDashboardProps,
} from '../../components/experience-dashboard/index.js';
import type { OvRequestOptions } from '../../client/types.js';
import { useExperienceDashboard } from './use-experience-dashboard.js';
import {
  EXPERIENCE_PAGE_SIZE,
  isExperienceDirectory,
} from './experience-data.js';

type Props = Pick<ExperienceDashboardProps, 'uri' | 'locale' | 'onOpenUri'> & {
  requestOptions?: OvRequestOptions;
  refreshGeneration?: number;
};
export function ExperienceDashboardPane({
  uri,
  locale,
  requestOptions,
  refreshGeneration,
  onOpenUri,
}: Props) {
  const browser = useExperienceDashboard({
    uri,
    requestOptions,
    refreshGeneration,
  });
  const directory = isExperienceDirectory(uri);
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
        outcomes: browser.outcomes.data,
        trajectories: browser.trajectories.data?.items,
        trajectoryTotal: browser.trajectories.data?.total,
        sources: browser.sources.data,
      }}
      tab={browser.tab}
      days={browser.days}
      page={browser.page}
      pageSize={EXPERIENCE_PAGE_SIZE}
      startIndex={browser.page * EXPERIENCE_PAGE_SIZE}
      hasMore={Boolean(
        directory
          ? browser.list.data?.hasMore
          : browser.tab === 'impact' && browser.trajectories.data?.has_more,
      )}
      states={{
        list: status(browser.list),
        sources: status(browser.sources),
        outcomes: status(browser.outcomes),
        trajectories: status(browser.trajectories),
      }}
      onTabChange={browser.setTab}
      onDaysChange={browser.setDays}
      onPageChange={browser.setPage}
    />
  );
}
