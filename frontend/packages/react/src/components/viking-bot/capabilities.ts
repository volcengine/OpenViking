import type { ReactNode } from 'react';
import type { FileTreeRow } from '../file-tree/index.js';

/** A host-provided prompt. Selecting it fills the draft; it never sends it. */
export interface VikingBotQuickInput {
  id: string;
  label: string;
  prompt: string;
  icon?: ReactNode;
}

/** A selected reference, kept separately from the editable text. */
export interface VikingBotReference {
  uri: string;
  name: string;
  type: 'file' | 'directory';
}

/** The host owns loading, hierarchy expansion and selected references. */
export interface VikingBotReferences {
  rows: readonly FileTreeRow[];
  selected: readonly VikingBotReference[];
  onChange: (references: readonly VikingBotReference[]) => void;
  onOpenChange?: (open: boolean) => void;
  onQueryChange?: (query: string) => void;
  onExpand?: (uri: string) => void;
  /** Return the pending work to deduplicate repeated load requests. */
  onLoadMore?: (parentId: string) => unknown;
  /** Return the pending work to deduplicate repeated retries. */
  onRetry?: (parentId?: string) => unknown;
  isLoading?: boolean;
  error?: string;
}

export interface VikingBotSession {
  id: string;
  title: string;
  /** Whether the session supports persisting a title through the Console provider. */
  canRename?: boolean;
  /** ISO date from the host's session API. Omit when unavailable. */
  updatedAt?: string;
}

/** Session persistence and requests belong to the host. */
export interface VikingBotSessions {
  items: readonly VikingBotSession[];
  /** Change this stable key or object reference when the host's account or session service changes. */
  scopeKey?: string | object;
  currentId?: string;
  /** Host-provided URI for locating the current session in the file browser. */
  currentUri?: string;
  isLoading?: boolean;
  error?: string;
  hasMore?: boolean;
  onOpenHistory?: () => void;
  /** Load the next page with no arguments. Return pending work to deduplicate requests. */
  onLoadMore?: () => void;
  /** Opt into cancellation for page requests. Takes precedence over onLoadMore. */
  onLoadMoreWithSignal?: (signal: AbortSignal) => unknown;
  onSelect: (id: string) => void;
  onNew: () => void;
  /** Called after the user enters a title. Return false if the change fails. */
  onRename?: (
    id: string,
    title: string,
  ) => Promise<boolean | void> | boolean | void;
  /** Called after the user confirms deletion. Return false if deletion fails. */
  onRemove?: (id: string) => Promise<boolean | void> | boolean | void;
}

export interface VikingBotInputFeatures {
  quickInputs?: readonly VikingBotQuickInput[];
  /** Override the default draft filling behavior. */
  onQuickInputSelect?: (input: VikingBotQuickInput) => void;
  references?: VikingBotReferences;
}
