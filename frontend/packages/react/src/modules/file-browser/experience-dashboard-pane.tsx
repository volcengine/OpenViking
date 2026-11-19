import { useRef, useState } from 'react';
import {
  ExperienceDashboard,
  type ExperienceDashboardProps,
  type ExperienceTrajectory,
} from '../../components/experience-dashboard/index.js';
import { ExperienceTrajectoryDetailPane } from './experience-trajectory-detail-pane.js';
import { useExperienceEffect } from './use-experience-effect.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import type { OvRequestOptions } from '../../client/types.js';
import { useExperienceDashboard } from './use-experience-dashboard.js';
import { useExperienceTrajectories } from './use-experience-trajectories.js';
import {
  EXPERIENCE_PAGE_SIZE,
  isExperienceDirectory,
} from './experience-data.js';

type Props = Pick<
  ExperienceDashboardProps,
  'uri' | 'locale' | 'onOpenUri' | 'slots'
> & {
  requestOptions?: OvRequestOptions;
  dataSource?: FileBrowserExperienceDataSource;
  refreshGeneration?: number;
};
export function ExperienceDashboardPane({
  uri,
  locale,
  dataSource,
  slots,
  requestOptions,
  refreshGeneration,
  onOpenUri,
}: Props) {
  const [selection, setSelection] = useState<{
    experienceUri: string;
    trajectory: ExperienceTrajectory;
  }>();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const loadMessages = dataSource?.trajectoryMessages;
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
  const effect = useExperienceEffect({
    uri,
    days: browser.days,
    loader: dataSource?.effect,
    requestOptions,
    enabled: !directory && browser.tab === 'impact',
    refreshGeneration,
  });
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
    <>
      <ExperienceDashboard
        uri={uri}
        kind={directory ? 'directory' : 'file'}
        locale={locale}
        slots={slots}
        onOpenUri={onOpenUri}
        onOpenTrajectory={
          loadMessages
            ? (trajectory, trigger) => {
                triggerRef.current = trigger;
                trigger.focus({ preventScroll: true });
                setSelection({ experienceUri: uri, trajectory });
              }
            : undefined
        }
        data={{
          experiences: browser.list.data?.items,
          total: directory
            ? browser.list.data?.total
            : browser.tests.data?.total,
          effect: effect.data,
          outcomes: browser.outcomes.data,
          trajectories: trajectories.data?.items,
          trajectoryTotal: trajectories.data?.total,
          sources: browser.sources.data?.items,
          sourceGroups: browser.sources.data?.groups,
          testMaterials:
            !directory && dataSource?.tests
              ? (browser.tests.data?.items ?? [])
              : undefined,
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
          effect: status(effect),
          list: status(browser.list),
          sources: status(browser.sources),
          tests: status(browser.tests),
          outcomes: status(browser.outcomes),
          trajectories: {
            loading: trajectories.loading,
            error: trajectories.error,
            onRetry: trajectories.retry,
          },
        }}
        onTabChange={(tab) => {
          setSelection(undefined);
          browser.setTab(tab);
        }}
        onDaysChange={browser.setDays}
        onPageChange={browser.setPage}
        onLoadMore={trajectories.loadMore}
      />
      {loadMessages &&
      selection?.experienceUri === uri &&
      browser.tab === 'source' ? (
        <ExperienceTrajectoryDetailPane
          key={selection.trajectory.uri}
          trajectory={selection.trajectory}
          triggerRef={triggerRef}
          requestOptions={requestOptions}
          loadMessages={loadMessages}
          locale={locale}
          onClose={() => setSelection(undefined)}
          onOpenUri={onOpenUri}
        />
      ) : null}
    </>
  );
}
