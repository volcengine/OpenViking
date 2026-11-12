import { useState } from 'react';

import { DashboardState } from '../../shared/dashboard/dashboard-state.js';
import { useLocale } from '../../shared/locale-context.js';
import {
  TabImpactIcon,
  TabSourceIcon,
  TabTestIcon,
} from '../../icons/index.js';
import { DashboardTabs } from './dashboard-tabs.js';
import { experienceLabels } from './labels.js';
import { ExperienceImpact } from './experience-impact.js';
import { ExperienceList } from './experience-list.js';
import { ExperiencePagination } from './experience-pagination.js';
import { ExperienceSources } from './experience-sources.js';
import { ExperienceTests } from './experience-tests.js';
import type {
  ExperienceDashboardTab,
  ExperienceDashboardProps,
} from './types.js';

export function ExperienceDashboard(props: ExperienceDashboardProps) {
  return <ExperienceDashboardContent key={props.uri} {...props} />;
}

function ExperienceDashboardContent({
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
  labels,
  className,
  ...rest
}: ExperienceDashboardProps) {
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
    setLocalTab(value);
    onTabChange?.(value);
  };
  const setDays = (value: number) => {
    setLocalDays(value);
    onDaysChange?.(value);
  };
  const t = experienceLabels(locale, labels);
  const directory = kind === 'directory';
  const tabs = [
    { key: 'impact' as const, title: t.impact, icon: <TabImpactIcon /> },
    { key: 'source' as const, title: t.source, icon: <TabSourceIcon /> },
    ...(missingTest
      ? []
      : [{ key: 'test' as const, title: t.test, icon: <TabTestIcon /> }]),
  ];
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
      onPageChange={onPageChange}
    />
  ) : null;
  return (
    <div
      {...rest}
      data-resource-uri={uri}
      className={['ov-experience-dashboard', className]
        .filter(Boolean)
        .join(' ')}
    >
      {directory ? (
        <div className="ov-experience-dashboard__body">
          <h2 className="ov-experience-dashboard__list-title">
            {t.allExperiences}
            {data.total === undefined ? '' : ` · ${data.total}`}
          </h2>
          {data.experiences?.length ? (
            <ExperienceList
              items={data.experiences}
              locale={locale}
              startIndex={startIndex}
              labels={t}
              onOpenUri={onOpenUri}
            />
          ) : (
            <DashboardState
              state={states.list}
              empty={t.empty}
              loading={t.loading}
              failed={t.failed}
              retry={t.retry}
            />
          )}
          {pagination}
        </div>
      ) : (
        <DashboardTabs
          tabs={tabs}
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
                outcomesState={states.outcomes ?? {}}
                trajectoriesState={states.trajectories ?? {}}
                hasMore={hasMore}
                loadingMore={loadingMore}
                // 显式回调优先；只有旧回调的宿主仍按文档约定翻页。
                // An explicit callback wins; an older-only host still pages as documented.
                onLoadMore={
                  onLoadMore ??
                  (onPageChange ? () => onPageChange(page + 1) : undefined)
                }
                onOpenUri={onOpenUri}
              />
            ) : tab === 'source' ? (
              data.sources?.length ? (
                <ExperienceSources
                  items={data.sources}
                  labels={t}
                  locale={locale}
                  onOpenUri={onOpenUri}
                />
              ) : (
                <DashboardState
                  state={states.sources}
                  empty={t.emptySource}
                  loading={t.loading}
                  failed={t.failed}
                  retry={t.retry}
                />
              )
            ) : data.testMaterials?.length ? (
              <>
                <ExperienceTests
                  items={data.testMaterials}
                  startIndex={startIndex}
                  labels={t}
                  onOpenUri={onOpenUri}
                />
                {pagination}
              </>
            ) : (
              <DashboardState
                state={states.tests}
                empty={t.emptyTests}
                loading={t.loading}
                failed={t.failed}
                retry={t.retry}
              />
            )}
          </div>
        </DashboardTabs>
      )}
    </div>
  );
}
