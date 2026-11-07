import { resolveInfoCopy } from './file-tree-info-labels.js';
import { resolveFileTreeInfoKey } from './file-tree-info.js';
import type { FileTreeEntryRow, FileTreeInfoLabels } from './types.js';

/** 浮层里各行的字段名默认值。Default field names used inside the tooltip. */
const DEFAULT_FIELDS = {
  description: 'Description',
  triggerBehavior: 'Trigger',
  path: 'Path',
} as const;

/**
 * 目录的默认浮层内容：说明、触发行为与路径。没有内置文案的路径返回 `null`，
 * 此时不显示浮层。
 *
 * 九类长期记忆的说明只出现在这里，因此这个浮层是它们唯一的展示位置。
 *
 * The default tooltip content for a directory: description, trigger copy and
 * path. Returns `null` when the path has no built-in copy, in which case no
 * tooltip shows.
 *
 * The nine long-term-memory keys describe themselves here only, so this tooltip
 * is the sole place their copy appears.
 */
export function FileTreeInfoTooltip({
  row,
  infoLabels,
}: {
  row: FileTreeEntryRow;
  infoLabels?: FileTreeInfoLabels;
}) {
  const key = resolveFileTreeInfoKey(row.uri);
  if (!key) return null;

  const copy = resolveInfoCopy(key, infoLabels);
  const fields = { ...DEFAULT_FIELDS, ...infoLabels?.field };

  if (!copy.description && !copy.triggerBehavior) return null;

  return (
    <span className="ov-file-tree__info">
      <span className="ov-file-tree__info-title">{row.name}</span>
      {copy.description ? (
        <span className="ov-file-tree__info-row">
          <span className="ov-file-tree__info-label">{fields.description}</span>
          <span className="ov-file-tree__info-value">{copy.description}</span>
        </span>
      ) : null}
      {copy.triggerBehavior ? (
        <span className="ov-file-tree__info-row">
          <span className="ov-file-tree__info-label">
            {fields.triggerBehavior}
          </span>
          <span className="ov-file-tree__info-value">
            {copy.triggerBehavior}
          </span>
        </span>
      ) : null}
      <span className="ov-file-tree__info-row">
        <span className="ov-file-tree__info-label">{fields.path}</span>
        <span className="ov-file-tree__info-path">{row.uri}</span>
      </span>
    </span>
  );
}
