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

/** Re-selecting a shortcut replaces a previous shortcut; custom text is kept. */
export function quickInputText(
  text: string,
  input: VikingBotQuickInput,
  previousPrompt?: string,
) {
  const start = previousPrompt ? text.lastIndexOf(previousPrompt) : -1;
  if (start >= 0) {
    const end = start + previousPrompt!.length;
    if (
      (start === 0 || text[start - 1] === '\n') &&
      (end === text.length || text[end] === '\n')
    )
      return text.slice(0, start) + input.prompt + text.slice(end);
  }
  if (!text.trim()) return input.prompt;
  return `${text}\n${input.prompt}`;
}
