import type { DirState, FsEntry } from './types.js';
import { decodePathLabel } from '../path-label.js';

export interface DirectoryEntryRow {
  kind: 'entry';
  id: string;
  depth: number;
  uri: string;
  name: string;
  type: 'directory' | 'file';
  expanded?: boolean;
  disabled?: boolean;
  selectable?: boolean;
  access?: 'read-only' | 'denied' | 'container';
  busy?: boolean;
  tags?: readonly string[];
}

export type DirectoryRow =
  | DirectoryEntryRow
  | {
      kind: 'loading' | 'load-more' | 'error' | 'empty';
      id: string;
      depth: number;
      parentId: string;
      loading?: boolean;
    };

/** 从 URI 取末段名称。最后一段为空时回退到整个 URI。 */
export function nameFromUri(uri: string): string {
  const trimmed = uri.replace(/\/+$/, '');
  const index = trimmed.lastIndexOf('/');
  const name = index < 0 ? trimmed : trimmed.slice(index + 1);
  return name || trimmed;
}

/** A readable URI-derived label; the original URI and basename keep their identity. */
export function displayNameFromUri(uri: string): string {
  return decodePathLabel(nameFromUri(uri));
}

/**
 * 把接口返回的一行映射成树行。目录未提供子节点信息，按「可以展开」处理。
 *
 * `index` 用于搜索结果：同一个 URI 可能出现在两个类别里，行 id 必须唯一，
 * 否则选中与滚动定位会命中错的行。
 *
 * Maps one API entry onto a tree row. A directory carries no child information,
 * so it is treated as expandable.
 *
 * `index` is for search results: one URI can appear in two categories, and row
 * ids must be unique or selection and scroll targeting hit the wrong row.
 */
export function toEntryRow(
  entry: FsEntry,
  depth: number,
  expanded: boolean,
  index?: number,
  busy?: boolean,
): DirectoryEntryRow {
  const isDir = entry.isDir === true;
  const access = entry.access === 'read' ? 'read-only' : entry.access;
  return {
    tags: entry.tags,
    kind: 'entry',
    id: index === undefined ? entry.uri : `${entry.uri}#${index}`,
    depth,
    uri: entry.uri,
    name: displayNameFromUri(entry.uri),
    type: isDir ? 'directory' : 'file',
    expanded: isDir ? expanded : undefined,
    disabled: entry.access === 'denied' || undefined,
    selectable: entry.access === 'container' ? false : undefined,
    access: access === 'write' || access === 'manage' ? undefined : access,
    // 正在取子节点时标记写入中，组件据此把展开箭头换成转圈。
    // While children load the row is marked busy, so the component swaps the
    // chevron for a spinner.
    busy: busy || undefined,
    // 行内说明一律交给组件：它按路径语义给出内置文案，未知路径就不显示。
    // 接口的 abstract 不进 `description`——`row.description` 的优先级高于内置
    // 文案，填了会把它盖住，而 abstract 是整段英文摘要，既混语言又挤满一行。
    //
    // Inline copy belongs to the component: it resolves built-in copy from the path
    // and shows none for an unknown one. The API's abstract never reaches
    // `description`, because `row.description` outranks the built-in copy and the
    // abstract is a full English paragraph — it would both mix languages and fill
    // the row.
  };
}

/** 把目录状态展开成按渲染顺序排列的行，只展开已打开的分支。 */
export function flattenTree(
  uri: string,
  depth: number,
  dirs: Record<string, DirState>,
  expanded: ReadonlySet<string>,
): DirectoryRow[] {
  const dir = dirs[uri];
  if (!dir) return [];
  const rows: DirectoryRow[] = [];

  for (const entry of dir.entries) {
    const isOpen = expanded.has(entry.uri);
    // 该目录的子节点正在加载：展开态但还没有结果。
    // The directory's children are loading: expanded, but no result yet.
    const loadingChildren = isOpen && dirs[entry.uri]?.loading === true;
    rows.push(toEntryRow(entry, depth, isOpen, undefined, loadingChildren));
    if (isOpen) {
      const children = flattenTree(entry.uri, depth + 1, dirs, expanded);
      if (children.length > 0) {
        rows.push(...children);
      } else if (loadingChildren) {
        // 首屏还在路上时给一行加载态，而不是空态：`entries` 此刻是空的，补空态
        // 就成了「目录为空」，而实际上只是还没回来。展开箭头上的转圈只管这一行，
        // 子节点区域空着会让人以为加载已经结束。
        //
        // While the first page is in flight the children area gets a loading row
        // rather than an empty one: `entries` is empty then, so an empty row would
        // claim the directory is empty when the result has simply not arrived. The
        // chevron spinner belongs to the row itself; an empty children area would
        // read as "already finished".
        rows.push({
          kind: 'loading',
          id: `${entry.uri}::loading`,
          depth: depth + 1,
          parentId: entry.uri,
        });
      } else {
        // 展开后确实没有子节点才补空态：不补的话这一行看起来和没展开一样，使用者
        // 会以为点击没生效。
        //
        // Only a directory that genuinely has no children gets the empty row:
        // without it the row looks identical to a collapsed one and the click seems
        // to do nothing.
        rows.push({
          kind: 'empty',
          id: `${entry.uri}::empty`,
          depth: depth + 1,
          parentId: entry.uri,
        });
      }
    }
  }

  if (dir.error) {
    rows.push({ kind: 'error', id: `${uri}::error`, depth, parentId: uri });
  } else if (dir.hasMore) {
    rows.push({
      kind: 'load-more',
      id: `${uri}::more`,
      depth,
      parentId: uri,
      loading: dir.loading,
    });
  }
  return rows;
}
