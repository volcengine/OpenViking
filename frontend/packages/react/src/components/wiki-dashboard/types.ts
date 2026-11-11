import type { HTMLAttributes, ReactNode } from 'react';

export interface WikiConflict {
  id: string;
  matchText: string;
  description: string;
  reason: string;
  severity: 'high' | 'medium' | 'low';
  state: 'unfixed' | 'fixed';
  beginTime: number;
  fixTime: number | null;
  fixPlan: string;
  resolutionBasis: string;
  resolutionSource?: 'manual' | 'automatic';
  location:
    | { status: 'unique'; start: number; end: number; line: number }
    | { status: 'missing' | 'ambiguous' };
}

export interface WikiDirectoryData {
  files: readonly string[];
  conflictFileCount?: number;
  limited?: boolean;
}
export interface WikiFileData {
  conflicts: readonly WikiConflict[];
  /** Document path to display; omitted paths hide the path column. */
  path?: string;
}
export type WikiDashboardData = WikiDirectoryData | WikiFileData;
export interface WikiDashboardAction {
  key: string;
  icon: ReactNode;
  tooltip: string;
  disabled?: boolean;
  onClick?: () => void;
}
export interface WikiDashboardLabels {
  title: string;
  pendingLabel: string;
  conflicts: string;
  conflictFiles: string;
  pending: string;
  all: string;
  severity: string;
  high: string;
  medium: string;
  low: string;
  fixed: string;
  reason: string;
  file: string;
  empty: string;
  loading: string;
  failed: string;
  retry: string;
  open: string;
  solution: string;
  occurred: string;
  fixedAt: string;
  evidence: string;
  limited: string;
}
interface WikiDashboardCommonProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onChange'
> {
  loading?: boolean;
  error?: Error | null;
  locale?: 'en-US' | 'zh-CN';
  onRetry?: () => void;
  labels?: Partial<WikiDashboardLabels>;
}

export type WikiDashboardProps = WikiDashboardCommonProps &
  (
    | {
        kind: 'directory';
        data?: WikiDirectoryData;
        onOpenUri?: (uri: string) => void;
        onOpenDashboard?: (uri: string) => void;
        /** Omitted or empty actions hide the row's action area. */
        defineActions?: (uri: string) => readonly WikiDashboardAction[];
      }
    | {
        kind: 'file';
        data?: WikiFileData;
        onOpenUri?: () => void;
        onOpenDashboard?: never;
        /** Hosts choose actions, including how they vary by conflict state. */
        defineActions?: (
          conflict: WikiConflict,
        ) => readonly WikiDashboardAction[];
      }
  );
