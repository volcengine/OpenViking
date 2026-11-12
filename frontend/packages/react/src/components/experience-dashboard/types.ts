import type { HTMLAttributes } from 'react';
import type { DashboardStatus } from '../../shared/dashboard/dashboard-state.js';

export interface ExperienceItem {
  uri: string;
  name: string;
  description?: string;
  modTime?: string;
  injectionCount?: number;
  recallCount?: number;
  trajectoryCount?: number;
}

export interface ExperienceTrajectory {
  uri: string;
  name: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
  /** 轨迹结果，提供后在标题右侧显示彩色状态点。Outcome shown beside the title. */
  outcome?: ExperienceOutcome;
}

export type ExperienceOutcome =
  'success' | 'partial' | 'unfinished' | 'failure' | 'unknown';

export interface ExperienceDashboardData {
  experiences?: readonly ExperienceItem[];
  total?: number;
  effect?: {
    injectionCount: number;
    recallCount: number;
    injectionRate: number;
  };
  outcomes?: readonly { outcome: ExperienceOutcome; count: number }[];
  trajectories?: readonly ExperienceTrajectory[];
  trajectoryTotal?: number;
  sources?: readonly ExperienceTrajectory[];
  testMaterials?: readonly {
    id: string;
    title: string;
    content: string;
    taskQuery?: string;
    uri?: string;
  }[];
}

export interface ExperienceDashboardLabels {
  title: string;
  allExperiences: string;
  index: string;
  name: string;
  impact: string;
  source: string;
  test: string;

  application: string;
  notice: string;
  injection: string;
  recall: string;
  listRecall: string;
  rate: string;
  injectionHint: string;
  recallHint: string;
  range: string;
  day3: string;
  week: string;
  month: string;
  all: string;

  outcomes: string;
  trajectories: string;
  emptyTimeline: string;

  latest: string;
  sourceCount: string;
  sourceUri: string;
  task: string;
  testMessage: string;
  trajectoryDetails: string;
  copy: string;
  copied: string;
  copyFailed: string;

  empty: string;
  emptySource: string;
  emptyTests: string;
  emptyOutcomes: string;
  loading: string;
  loadingMore: string;
  loadMore: string;
  failed: string;
  retry: string;
  previous: string;
  next: string;
  page: string;
  jumpPrevious: string;
  jumpNext: string;
  updated: string;
  success: string;
  partial: string;
  unfinished: string;
  failure: string;
  unknown: string;
  unsupportedMetric: string;
  open: string;
}

export interface ExperienceDashboardProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onChange'
> {
  uri: string;
  kind: 'directory' | 'file';
  data?: ExperienceDashboardData;
  tab?: ExperienceDashboardTab;
  days?: number;
  page?: number;
  pageSize?: number;
  hasMore?: boolean;
  /** 追加下一页的请求正在进行。 A further page is in flight. */
  loadingMore?: boolean;
  startIndex?: number;
  states?: Partial<
    Record<
      'list' | 'outcomes' | 'trajectories' | 'sources' | 'tests',
      ExperienceDashboardState
    >
  >;
  locale?: 'en-US' | 'zh-CN';
  onTabChange?: (tab: ExperienceDashboardTab) => void;
  onDaysChange?: (days: number) => void;
  onPageChange?: (page: number) => void;
  /** 追加下一页的轨迹；省略时加载更多按钮禁用。 Appends the next page; disabled when omitted. */
  onLoadMore?: () => void;
  onOpenUri?: (uri: string) => void;
  labels?: Partial<ExperienceDashboardLabels>;
}

export type ExperienceDashboardTab = 'impact' | 'source' | 'test';
export type ExperienceDashboardState = DashboardStatus;
