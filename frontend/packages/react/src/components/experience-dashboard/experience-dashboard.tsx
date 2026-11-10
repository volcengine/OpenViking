import { DashboardState } from '../../shared/dashboard/dashboard-state.js';
import { useLocale } from '../../shared/locale-context.js';
import { DashboardTabs } from './dashboard-tabs.js';
import { experienceLabels } from './labels.js';
import { ExperienceList } from './experience-list.js';
import { ExperienceImpact } from './experience-impact.js';
import { ExperienceTests } from './experience-tests.js';
import { ExperienceTrajectories } from './experience-trajectories.js';
import { useState } from 'react';
import { ExperiencePagination } from './experience-pagination.js';
import type {
  ExperienceDashboardTab,
  ExperienceDashboardProps,
} from './types.js';

export function ExperienceDashboard(props: ExperienceDashboardProps) {
  return (
    <ExperienceDashboardContent
      key={`${props.uri}:${props.data?.testMaterials !== undefined}`}
      {...props}
    />
  );
}

function ExperienceDashboardContent({
  uri,
  data = {},
  kind,
  tab: controlledTab,
  days: controlledDays,
  page = 0,
  hasMore = false,
  startIndex = 0,
  states = {},
  onTabChange,
  onDaysChange,
  onPageChange,
  locale: localeProp,
  onOpenUri,
  labels,
  className,
  ...rest
}: ExperienceDashboardProps) {
  const locale = useLocale(localeProp);
  const [localTab, setLocalTab] = useState<ExperienceDashboardTab>('impact');
  const [localDays, setLocalDays] = useState(7);
  const tab = controlledTab ?? localTab;
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
  const result = data;
  const tabs = [
    { key: 'impact' as const, title: t.impact },
    { key: 'source' as const, title: t.source },
    ...(result.testMaterials === undefined
      ? []
      : [{ key: 'test' as const, title: t.test }]),
  ];
  const activeState = directory
    ? states.list
    : tab === 'source'
      ? states.sources
      : states.trajectories;
  const pagination = (
    <ExperiencePagination
      page={page}
      hasMore={hasMore}
      loading={activeState?.loading}
      labels={t}
      onPageChange={onPageChange}
    />
  );
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
          {result.experiences?.length ? (
            <ExperienceList
              items={result.experiences}
              total={result.total}
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
              <>
                <ExperienceImpact
                  data={result}
                  labels={t}
                  days={days}
                  onDaysChange={setDays}
                  outcomesState={states.outcomes ?? {}}
                  trajectoriesState={states.trajectories ?? {}}
                  onOpenUri={onOpenUri}
                />
                {pagination}
              </>
            ) : tab === 'source' ? (
              result.sources?.length ? (
                <details open className="ov-experience-dashboard__source-group">
                  <summary>
                    <strong>{t.latest}</strong>
                    <span>
                      {t.sourceCount.replace(
                        '{count}',
                        String(result.sources.length),
                      )}
                    </span>
                  </summary>
                  <ExperienceTrajectories
                    items={result.sources}
                    source
                    onOpenUri={onOpenUri}
                  />
                </details>
              ) : (
                <DashboardState
                  state={states.sources}
                  empty={t.emptySource}
                  loading={t.loading}
                  failed={t.failed}
                  retry={t.retry}
                />
              )
            ) : result.testMaterials?.length ? (
              <ExperienceTests
                items={result.testMaterials}
                labels={t}
                onOpenUri={onOpenUri}
              />
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
