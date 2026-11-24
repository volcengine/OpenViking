import type { Fault } from './fault.js';
import type { Ready } from './views.js';

export type PollFields = {
  readonly taskRef: string;
  readonly outageId: string;
  readonly queryAttempt: number;
  readonly consecutiveFailures: number;
};

export type PollRequestFault = Fault<
  | 'network'
  | 'http'
  | 'business'
  | 'configuration'
  | 'response-read'
  | 'host-service'
  | 'unknown'
> &
  PollFields;

export type PollProtocolFault = Fault<'protocol'> & PollFields;

export type PollRecovered = Ready &
  Omit<PollFields, 'consecutiveFailures'> & {
    readonly consecutiveFailures: 0;
    readonly outageDurationMs: number;
    readonly failureCount: number;
    readonly failedCategories: readonly ('request' | 'protocol')[];
  };
