import {
  isHomeResourceUri,
  isUserResourceUri,
  resourceUriIdentity,
} from '../resource-uri.js';
import { mayShareHomeBranch } from './expansion-path.js';
import type { DirectoryExpansionEdit } from './types.js';

export interface ExpansionState {
  expanded: ReadonlySet<string>;
  collapsed: ReadonlySet<string>;
  edited: boolean;
  pendingEdits: readonly DirectoryExpansionEdit[];
}

export function matchesIdentity(uri: string, home?: string) {
  const key = resourceUriIdentity(uri, home);
  return (candidate: string) => resourceUriIdentity(candidate, home) === key;
}

export function underCollapse(uri: string, collapsed: ReadonlySet<string>) {
  return [...collapsed].some(
    (fold) => uri === fold || uri.startsWith(`${fold}/`),
  );
}

export function unresolvedFold(state: ExpansionState, uri: string) {
  const matches = matchesIdentity(uri);
  return (
    !state.pendingEdits.some((edit) => edit.expanded && matches(edit.uri)) &&
    [...state.collapsed].some((fold) => mayShareHomeBranch(fold, uri))
  );
}

export function applyExpansionEdit(
  state: ExpansionState,
  { uri, expanded: open }: DirectoryExpansionEdit,
  home?: string,
): ExpansionState {
  const matches = matchesIdentity(uri, home);
  const other = (candidate: string) => !matches(candidate);
  const expanded = new Set([...state.expanded].filter(other));
  const collapsed = new Set([...state.collapsed].filter(other));
  if (open) expanded.add(uri);
  else collapsed.add(uri);
  const pendingEdits =
    !home && (isHomeResourceUri(uri) || isUserResourceUri(uri))
      ? [
          ...state.pendingEdits.filter((edit) => other(edit.uri)),
          { uri, expanded: open },
        ]
      : state.pendingEdits;
  return { expanded, collapsed, edited: true, pendingEdits };
}

export function settleExpansionEdits(state: ExpansionState, home?: string) {
  if (!home || !state.pendingEdits.length) return state;
  // Replay in edit order only after the scoped alias mapping is authoritative.
  return state.pendingEdits.reduce<ExpansionState>(
    (before, edit) => applyExpansionEdit(before, edit, home),
    { ...state, pendingEdits: [] },
  );
}
