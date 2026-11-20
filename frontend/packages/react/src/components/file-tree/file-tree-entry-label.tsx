import { resolveInfoCopy } from './file-tree-info-labels.js';
import {
  resolveFileTreeInfoKey,
  showsInlineDescription,
} from './file-tree-info.js';
import type { FileTreeEntryRow, FileTreeInfoLabels } from './types.js';

export function FileTreeEntryLabel({
  row,
  infoLabels,
  sharedLabel,
}: {
  row: FileTreeEntryRow;
  infoLabels?: FileTreeInfoLabels;
  sharedLabel: string;
}) {
  const infoKey = resolveFileTreeInfoKey(row.uri);
  const shared =
    row.type === 'directory' &&
    row.depth === 0 &&
    infoKey === 'sharedResources';
  // Keep explicit host descriptions, including empty strings.
  const description =
    row.description ??
    (infoKey && showsInlineDescription(infoKey)
      ? resolveInfoCopy(infoKey, infoLabels).description
      : undefined);

  return (
    <span
      className="ov-file-tree__label-group"
      data-description={Boolean(description) || undefined}
    >
      <span className="ov-file-tree__label">{row.name}</span>
      {shared && sharedLabel ? (
        <span className="ov-file-tree__shared">{sharedLabel}</span>
      ) : null}
      {description ? (
        <span className="ov-file-tree__description">{description}</span>
      ) : null}
    </span>
  );
}
