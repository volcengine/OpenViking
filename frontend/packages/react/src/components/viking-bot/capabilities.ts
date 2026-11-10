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
  onQueryChange?: (query: string) => void;
  onExpand?: (uri: string) => void;
  isLoading?: boolean;
  error?: string;
}

export interface VikingBotSession {
  id: string;
  title: string;
  /** ISO date from the host's session API. Omit when unavailable. */
  updatedAt?: string;
}

/** Session persistence and requests belong to the host. */
export interface VikingBotSessions {
  /** Host actions beside a session, for example a deletion confirmation. */
  renderAction?: (session: VikingBotSession) => ReactNode;
  /** Disable creation while the host creates a session. */
  isCreating?: boolean;
  /** Hide creation when the current draft is already empty. */
  canCreate?: boolean;
  items: readonly VikingBotSession[];
  currentId?: string;
  isLoading?: boolean;
  error?: string;
  hasMore?: boolean;
  onOpenHistory?: () => void;
  onLoadMore?: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
}

export interface VikingBotInputFeatures {
  quickInputs?: readonly VikingBotQuickInput[];
  /** Override the default draft filling behavior. */
  onQuickInputSelect?: (input: VikingBotQuickInput) => void;
  references?: VikingBotReferences;
}
