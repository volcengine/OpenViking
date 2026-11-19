import type { ErrorFallback } from '../../shared/error-boundary/types.js';
import type { Locale } from '../../shared/locale.js';
import type { HTMLAttributes } from 'react';
import type {
  ScopeSwitcherLabels,
  ScopeSwitcherOption,
} from '../../components/scope-switcher/index.js';

export interface ScopeCascadeApi {
  loadLibraries: (
    signal: AbortSignal,
  ) => Promise<readonly ScopeSwitcherOption[]>;
  loadSpaces: (
    libraryId: string,
    signal: AbortSignal,
  ) => Promise<readonly ScopeSwitcherOption[]>;
  loadUsers: (
    libraryId: string,
    spaceId: string,
    signal: AbortSignal,
  ) => Promise<readonly ScopeSwitcherOption[]>;
}
export type ScopeCascadeIndex = 0 | 1 | 2;
export interface ScopeCascadeSnapshot {
  readonly options: readonly ScopeSwitcherOption[];
  readonly selected?: ScopeSwitcherOption;
  readonly enabled: boolean;
  readonly loading: boolean;
  readonly error: Error | null;
  /** Changes only when ancestor identities change. */
  readonly scopeKey: string;
  /** Ancestor IDs in library / space order, available to downstream consumers. */
  readonly parentIds: readonly string[];
}
export interface ScopeCascadeLevel {
  getSnapshot: () => ScopeCascadeSnapshot;
  subscribe: (listener: () => void) => () => void;
  select: (option: ScopeSwitcherOption) => void;
  refresh: () => void;
}
export interface ScopeCascadeController {
  readonly levels: readonly [
    ScopeCascadeLevel,
    ScopeCascadeLevel,
    ScopeCascadeLevel,
  ];
}
export interface ScopeCascadeProps
  extends
    Omit<HTMLAttributes<HTMLDivElement>, 'children' | 'onChange'>,
    ScopeCascadeApi {
  /** Optional locale; inherits the nearest module or Provider. */
  locale?: Locale;
  /** Accessible names and placeholders, in library / space / user order. */
  levelNames?: readonly [string, string, string];
  retryLabel?: string;
  onChange?: (selection: ScopeCascadeSelection | null) => void;
  /** 渲染异常时的替代内容，省略时继承外层或使用内置展示。Render-failure content; inherits or uses the default when omitted. */
  slots?: { errorFallback?: ErrorFallback };
  labels?: ScopeSwitcherLabels;
}

export interface ScopeCascadeSelection {
  libraryId: string;
  spaceId: string;
  userId: string;
}
