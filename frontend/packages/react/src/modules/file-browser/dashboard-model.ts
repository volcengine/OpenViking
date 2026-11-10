import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import { isExperienceDirectory, isExperienceFile } from './experience-data.js';

export interface FileBrowserDashboard {
  kind: 'experience' | 'wiki';
  uri: string;
  pendingConflicts?: boolean;
  isRoot?: boolean;
}
export const dashboardKey = (uri: string) => `dashboard:${uri}`;
export const within = (uri: string, root: string) =>
  uri === root || uri.startsWith(`${root.replace(/\/+$/, '')}/`);

/** Metadata from the resource wins over a missing/stale configuration registry. */
export function resolveDashboard(
  uri: string,
  rows: readonly FileTreeRow[],
  targets: readonly { to: string; conflicts_num?: number }[],
): FileBrowserDashboard | undefined {
  if (isExperienceDirectory(uri) || isExperienceFile(uri))
    return { kind: 'experience', uri };
  const entries = rows.filter(
    (row): row is FileTreeEntryRow & { tags?: readonly string[] } =>
      row.kind === 'entry',
  );
  const row = entries.find((item) => item.uri === uri);
  const hasWiki = entries.some(
    (item) =>
      item.tags?.includes('compile_target=true') && within(uri, item.uri),
  );
  const owner = targets
    .filter((item) => within(uri, item.to))
    .sort((a, b) => b.to.length - a.to.length)[0];
  if (!hasWiki && !owner) return undefined;
  const tagged = row?.tags?.find((tag) => tag.startsWith('has_conflict='));
  return {
    kind: 'wiki',
    uri,
    isRoot: Boolean(
      row?.tags?.includes('compile_target=true') || owner?.to === uri,
    ),
    pendingConflicts: tagged
      ? tagged === 'has_conflict=true'
      : owner?.to === uri && owner.conflicts_num !== undefined
        ? owner.conflicts_num > 0
        : undefined,
  };
}
