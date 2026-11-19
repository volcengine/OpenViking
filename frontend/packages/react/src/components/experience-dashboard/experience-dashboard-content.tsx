import { useDashboardAnalytics } from './use-dashboard-analytics.js';
import { useTrack } from '../../shared/analytics/use-track.js';
import { useExposure } from '../../shared/analytics/use-exposure.js';
import { useState } from 'react';

import { useLocale } from '../../shared/locale-context.js';
import { DashboardTabs, experienceTabs } from './dashboard-tabs.js';
import { experienceLabels } from './labels.js';
import { ExperienceQueryState } from './experience-state.js';
import { createSectionRenderer } from './render-section.js';
import { ExperienceImpact } from './experience-impact.js';
import { ExperienceDirectory } from './experience-directory.js';
import { ExperiencePagination } from './experience-pagination.js';
import { ExperienceSources } from './experience-sources.js';
import { ExperienceTests } from './experience-tests.js';
import type {
  ExperienceDashboardTab,
  ExperienceDashboardProps,
} from './types.js';

export function ExperienceDashboardContent({
  uri,
  data = {},
  kind,
  tab: controlledTab,
  days: controlledDays,
  page = 0,
  pageSize = 20,
  hasMore = false,
  loadingMore = false,
  startIndex = 0,
  states = {},
  onTabChange,
  onDaysChange,
  onPageChange,
  onLoadMore,
  locale: localeProp,
  onOpenUri,
  onOpenTrajectory,
  labels,
  slots,
  className,
  ...rest
}: ExperienceDashboardProps) {
  const track = useTrack();
  const events = useDashboardAnalytics({ onOpenUri, onPageChange });
  const exposure = useExposure('experience_dashboard_show', uri);
  const locale = useLocale(localeProp);
  const [localTab, setLocalTab] = useState<ExperienceDashboardTab>('impact');
  const [localDays, setLocalDays] = useState(7);
  const selectedTab = controlledTab ?? localTab;
  const missingTest = data.testMaterials === undefined;
  const tab = selectedTab === 'test' && missingTest ? 'impact' : selectedTab;
  // Only an unavailable Test selection expires; material updates keep other state.
  if (localTab === 'test' && missingTest) setLocalTab('impact');
  const days = controlledDays ?? localDays;
  const setTab = (value: ExperienceDashboardTab) => {
    if (value !== tab) track('experience_dashboard_tab_change');
    setLocalTab(value);
    onTabChange?.(value);
  };
  const setDays = (value: number) => {
    if (value !== days) track('experience_dashboard_range_change');
    setLocalDays(value);
    onDaysChange?.(value);
  };
  const t = experienceLabels(locale, labels);
  const directory = kind === 'directory';
  const activeState = directory
    ? states.list
    : tab === 'source'
      ? states.sources
      : tab === 'test'
        ? states.tests
        : states.trajectories;
  // 只有目录视图与「测试」页签用页码器；「应用效果」的轨迹按游标翻页，用滚动
  // 加载更多，与火山版一致。
  //
  // Only the directory view and the Test tab page by number; Impact's trajectories
  // page by cursor and use load-more, matching the console.
  const paged = directory || tab === 'test';
  // 有总数时页数由总数换算，这里不传；没有总数时以「当前页之后还有没有下一页」
  // 兜底。两种情况取值不同（一个是要传的数、一个是不传），不是同一种兜底，因此
  // 保留三元而不是 `??`。
  //
  // With a total the page count comes from it, so nothing is passed here; without one
  // the "there is a next page" flag bounds it. The two branches mean different things —
  // a value versus no value — so this stays a ternary rather than `??`.
  const pageCount =
    data.total === undefined ? page + (hasMore ? 2 : 1) : undefined;
  const pagination = paged ? (
    <ExperiencePagination
      page={page}
      pageCount={pageCount}
      total={data.total}
      pageSize={pageSize}
      loading={activeState?.loading}
      labels={t}
      onPageChange={events.page}
    />
  ) : null;
  const loadMore =
    onLoadMore ?? (onPageChange ? () => onPageChange(page + 1) : undefined);
  const renderSection = createSectionRenderer(
    slots?.sections,
    {
      uri,
      data,
      days,
      locale,
      page,
      pageSize,
      startIndex,
      hasMore,
      loadingMore,
      onPageChange: events.page,
      onLoadMore: loadMore,
      onOpenUri,
      onOpenTrajectory,
    },
    states,
    events,
  );
  return (
    <div
      {...rest}
      ref={exposure}
      data-resource-uri={uri}
      className={['ov-experience-dashboard', className]
        .filter(Boolean)
        .join(' ')}
    >
      {directory ? (
        <ExperienceDirectory
          data={data}
          labels={t}
          locale={locale}
          page={page}
          pageSize={pageSize}
          startIndex={startIndex}
          state={states.list}
          onOpenUri={events.experience}
          renderList={(content) => renderSection('list', content)}
          pagination={pagination}
        />
      ) : (
        <DashboardTabs
          tabs={experienceTabs(t, missingTest)}
          active={tab}
          label={t.title}
          onChange={setTab}
        >
          <div className="ov-experience-dashboard__body">
            {tab === 'impact' ? (
              <ExperienceImpact
                data={data}
                labels={t}
                locale={locale}
                days={days}
                onDaysChange={setDays}
                effectState={states.effect ?? {}}
                outcomesState={states.outcomes ?? {}}
                trajectoriesState={states.trajectories ?? {}}
                hasMore={hasMore}
                loadingMore={loadingMore}
                onLoadMore={loadMore}
                renderSection={renderSection}
                onOpenUri={events.trajectory}
              />
            ) : tab === 'source' ? (
              renderSection(
                'sources',
                <ExperienceSources
                  groups={data.sourceGroups}
                  items={data.sources}
                  state={states.sources}
                  onOpenTrajectory={onOpenTrajectory}
                  labels={t}
                  locale={locale}
                  onOpenUri={events.source}
                />,
              )
            ) : (
              <>
                {renderSection(
                  'tests',
                  data.testMaterials?.length ? (
                    <ExperienceTests
                      items={data.testMaterials}
                      startIndex={startIndex}
                      labels={t}
                      onOpenUri={events.test}
                    />
                  ) : (
                    <ExperienceQueryState
                      state={states.tests}
                      rows={4}
                      empty={t.emptyTests}
                      loading={t.loading}
                      failed={t.failed}
                      retry={t.retry}
                    />
                  ),
                )}
                {data.testMaterials?.length ? pagination : null}
              </>
            )}
          </div>
        </DashboardTabs>
      )}
    </div>
  );
}
