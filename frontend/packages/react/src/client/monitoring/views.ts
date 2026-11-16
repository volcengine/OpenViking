import type { ErrorKind, Fault } from './fault.js';

export type Ready = {
  readonly durationMs: number;
  readonly visibility: 'visible' | 'hidden';
};

export type RenderFault = Fault<'render'>;

export type CodeReady = Ready & {
  readonly cacheState: 'cached' | 'uncached' | 'unknown';
};

export type CodeFault = Fault<'resource-load' | 'configuration' | 'unknown'>;

export type MediaReady = Ready & {
  readonly cacheState: 'cached' | 'uncached' | 'unknown';
};

export type MediaFault = Fault<'resource-load' | 'unknown'>;

export type OperationFault = Fault<ErrorKind>;

export type DirectoryReady = Ready & {
  readonly entryCount: number;
  readonly empty: boolean;
};

export type PreviewReady = Ready & {
  readonly contentKind: 'text' | 'image' | 'video' | 'document' | 'other';
  readonly empty: boolean;
};

export type SearchReady = Ready & {
  readonly resultCount: number;
  readonly hasMore: boolean | null;
};

export type DashboardReady = Ready & { readonly empty: boolean };

export type TrajectoryReady = Ready & { readonly itemCount: number };

export type SessionListReady = Ready & { readonly sessionCount: number };

export type SessionReady = Ready;

export type HistoryReady = Ready & { readonly messageCount: number };

export type OptionsReady = Ready & { readonly optionCount: number };

export type ContentReady = Ready & {
  readonly empty: boolean;
  readonly viewMode: 'source' | 'preview';
};

export type FirstContentReady = Ready & {
  readonly contentKind: 'text' | 'reasoning' | 'tool';
};

export type ReplyReady = Ready & { readonly firstContentObserved: boolean };

export type ReplyFault = Fault<
  | 'configuration'
  | 'network'
  | 'http'
  | 'business'
  | 'response-read'
  | 'protocol'
  | 'host-service'
  | 'renderer'
  | 'unknown'
>;

export type PersistenceFault = Fault<'storage' | 'host-service' | 'unknown'>;
