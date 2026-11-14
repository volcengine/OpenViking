import type { PollState } from './monitoring.js';
import type { TraceSnapshot } from '../../client/monitoring/trace.js';
import type { MonitoringBinding } from '../monitoring/binding.js';
export type TaskStatus =
  'pending' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled';

export interface TaskReference {
  id: string;
  kind?: 'resource' | 'skill' | 'memory' | 'compile';
  uri?: string;
  /** A task status, distinct from the submission response's status. */
  status?: TaskStatus;
}

export interface TaskSnapshot extends TaskReference {
  stage?: string;
  result?: unknown;
  error?: string;
  /** Query failures never change the backend task status. */
  queryError?: { code: 'request' | 'invalid-response'; message?: string };
}

export interface TaskService {
  getTask: (id: string, signal: AbortSignal) => Promise<TaskSnapshot>;
}

export interface TaskSource {
  monitoring?: MonitoringBinding;
  key: string;
  service: TaskService;
  identity: readonly unknown[];
  owners: number;
}

export interface TrackedTask extends TaskSnapshot {
  key: string;
}

export const isTaskTerminal = (status?: TaskStatus) =>
  status === 'completed' || status === 'failed' || status === 'cancelled';

export const taskStatuses: readonly string[] = [
  'pending',
  'running',
  'cancelling',
  'completed',
  'failed',
  'cancelled',
];

export interface TaskTrackerEntry {
  monitoring?: PollState;
  origin?: TraceSnapshot['links'][number];
  monitoringRelease?: () => void;
  observedAtStart?: boolean;
  source: TaskSource;
  snapshot: TrackedTask;
  due: number;
  failures: number;
  request?: AbortController;
}
