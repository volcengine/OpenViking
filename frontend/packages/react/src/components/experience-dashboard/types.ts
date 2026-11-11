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
  latest: string;
  sourceCount: string;
  sourceUri: string;
  task: string;
  copy: string;
  copied: string;
  copyFailed: string;

  title: string;
  allExperiences: string;
  index: string;
  impact: string;
  source: string;
  test: string;
  application: string;
  notice: string;
  injection: string;
  recall: string;
  listRecall: string;
  rate: string;
  outcomes: string;
  trajectories: string;
  empty: string;
  emptySource: string;
  emptyTests: string;
  emptyOutcomes: string;
  loading: string;
  failed: string;
  retry: string;
  previous: string;
  next: string;
  all: string;
  week: string;
  month: string;
  range: string;
  updated: string;
  name: string;
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
  hasMore?: boolean;
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
  onOpenUri?: (uri: string) => void;
  labels?: Partial<ExperienceDashboardLabels>;
}

export type ExperienceDashboardTab = 'impact' | 'source' | 'test';
export type ExperienceDashboardState = DashboardStatus;
