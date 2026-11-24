import { resolveInfoCopy } from './file-tree-info-labels.js';
import { resolveFileTreeInfoKey } from './file-tree-info.js';
import type { FileTreeEntryRow, FileTreeInfoLabels } from './types.js';

/** 浮层里各行的字段名默认值。Default field names used inside the popover. */
const DEFAULT_FIELDS = {
  description: 'Description',
  triggerBehavior: 'Trigger',
  path: 'Path',
} as const;

export interface FileTreeInfoCopy {
  /** 行名称，作为浮层标题。Row name, used as the popover title. */
  title: string;
  /** 说明，已按行内同样的优先级解析。Description, resolved with the same precedence as inline. */
  description?: string;
  /** 触发行为，只出现在浮层。Trigger copy, popover only. */
  triggerBehavior?: string;
  /** 完整路径。Full path. */
  path: string;
  /** 浮层里各行的字段名。Field names used inside the popover. */
  fields: { description: string; triggerBehavior: string; path: string };
}

/**
 * 解析目录浮层要显示的内容，没有可显示内容时返回 `null`。
 *
 * 说明按 `row.description` → 内置文案的顺序解析，与行内一致：宿主给了就用宿主的
 * （含显式空串），没给才用内置。调用方据此决定是否创建浮层外壳，避免留下一个
 * 只有背景和内边距的空壳。
 *
 * Resolves what a directory popover should show, or `null` when there is nothing.
 *
 * The description resolves as `row.description` then built-in copy, matching the
 * inline behaviour: a host value wins, including an explicit empty string, and
 * the built-in copy applies only when absent. The caller uses this to decide
 * whether to create the surface, so no empty shell is left behind.
 */
export function resolveFileTreeInfoCopy(
  row: FileTreeEntryRow,
  infoLabels?: FileTreeInfoLabels,
): FileTreeInfoCopy | null {
  const key = resolveFileTreeInfoKey(row.uri);
  // 没有内置指引的目录不弹浮层：接口给每个目录都返回了 abstract，拿它当说明会
  // 让任意目录都浮出一张只有路径的卡片，与设计稿不符。指引只对已知语义的路径
  // 提供，其余目录的 abstract 已经在行内作为副标题显示。
  //
  // A directory without built-in guidance gets no popover: the API returns an
  // abstract for every directory, and treating it as the description would pop a
  // path-only card on any row, which the design does not do. Guidance exists only
  // for paths with known semantics; other abstracts already show inline.
  if (!key) return null;

  const builtIn = resolveInfoCopy(key, infoLabels);
  const description = row.description ?? builtIn.description;

  // 只有路径时不算内容，否则文件也会有一个只显示路径的浮层。
  // A path alone does not count as content, or files would show a popover too.
  if (!description && !builtIn.triggerBehavior) return null;

  return {
    title: row.name,
    description,
    triggerBehavior: builtIn.triggerBehavior,
    path: row.uri,
    fields: { ...DEFAULT_FIELDS, ...infoLabels?.field },
  };
}

/**
 * 目录的默认浮层内容：说明、触发行为与路径。九类长期记忆的说明只出现在这里，
 * 因此这个浮层是它们唯一的展示位置。
 *
 * The default popover content for a directory: description, trigger copy and
 * path. The nine long-term-memory keys describe themselves here only, so this
 * surface is the sole place their copy appears.
 */
export function FileTreeInfoTooltip({ copy }: { copy: FileTreeInfoCopy }) {
  const { description, fields, path, title, triggerBehavior } = copy;

  return (
    <div className="ov-file-tree__info">
      <div className="ov-file-tree__info-title">{title}</div>
      {description ? (
        <div className="ov-file-tree__info-row">
          <span className="ov-file-tree__info-label">{fields.description}</span>
          <span className="ov-file-tree__info-value">{description}</span>
        </div>
      ) : null}
      {triggerBehavior ? (
        <div className="ov-file-tree__info-row">
          <span className="ov-file-tree__info-label">
            {fields.triggerBehavior}
          </span>
          <span className="ov-file-tree__info-value">{triggerBehavior}</span>
        </div>
      ) : null}
      <div className="ov-file-tree__info-row">
        <span className="ov-file-tree__info-label">{fields.path}</span>
        <span className="ov-file-tree__info-path">{path}</span>
      </div>
    </div>
  );
}
