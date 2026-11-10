import type { HTMLAttributes } from 'react';

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
export interface WikiDashboardLabels {
  title: string;
  pendingLabel: string;
  discuss: string;
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
  edit: string;
  solution: string;
  occurred: string;
  fixedAt: string;
  evidence: string;
  history: string;
  historyHint: string;
  unavailable: string;
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
        editDisabledReason?: never;
        discussDisabledReason?: never;
        onOpenUri?: (uri: string) => void;
        onOpenDashboard?: (uri: string) => void;
        onEdit?: (uri: string) => void;
        onDiscuss?: never;
      }
    | {
        kind: 'file';
        data?: WikiFileData;
        /** Explanation shown when editing has no handler. Defaults to labels.unavailable. */
        editDisabledReason?: string;
        /** Explanation shown when discussion has no handler. Defaults to labels.unavailable. */
        discussDisabledReason?: string;
        onOpenUri?: () => void;
        onOpenDashboard?: never;
        onEdit?: (conflict: WikiConflict) => void;
        onDiscuss?: (conflict: WikiConflict) => void;
      }
  );
