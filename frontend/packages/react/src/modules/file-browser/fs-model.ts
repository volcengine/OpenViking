import type {
  FilePreviewDirectoryTab,
  FilePreviewFileTab,
} from '../../components/file-preview/index.js';
import type { ReactNode } from 'react';

import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';

/**
 * 目录接口返回的一行。`ls` 的 `output=agent` 不返回 `name`，名称从 URI 末段取。
 * One row from the directory API. `ls` with `output=agent` returns no `name`, so
 * the name comes from the last URI segment.
 */
export interface FsEntry {
  uri: string;
  isDir?: boolean;
  size?: number;
  modTime?: string;
  abstract?: string;
}

/** 一个目录已加载的子节点与分页状态。 */
export interface DirState {
  entries: FsEntry[];
  hasMore: boolean;
  loading: boolean;
  error: Error | null;
}

/** 语义搜索返回的分组结果。Grouped results of a semantic search. */

/** 空目录状态。An empty directory state. */
export const emptyDir = (): DirState => ({
  entries: [],
  hasMore: false,
  loading: false,
  error: null,
});

/**
 * 把新到的一页并入目录状态：`offset` 为 0 是首屏、直接替换，否则追加到已有
 * 子节点之后。
 *
 * 追加而不是替换，是因为分页要保留前几页；替换会让「加载更多」把列表截成最后
 * 一页。
 *
 * Merges an arriving page into a directory's state: `offset` 0 is the first page and
 * replaces, anything else appends to the children already loaded.
 *
 * It appends rather than replaces because paging has to keep the earlier pages;
 * replacing would truncate the list to its last page on "load more".
 */
export function mergePage(
  current: DirState | undefined,
  page: { entries: FsEntry[]; hasMore: boolean },
  offset: number,
): DirState {
  const previous = current ?? emptyDir();
  return {
    entries:
      offset === 0 ? page.entries : [...previous.entries, ...page.entries],
    hasMore: page.hasMore,
    loading: false,
    error: null,
  };
}

/** 从 URI 取末段名称。最后一段为空时回退到整个 URI。 */
export function nameFromUri(uri: string): string {
  const trimmed = uri.replace(/\/+$/, '');
  const index = trimmed.lastIndexOf('/');
  const name = index < 0 ? trimmed : trimmed.slice(index + 1);
  return name || trimmed;
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
): FileTreeEntryRow {
  const isDir = entry.isDir === true;
  return {
    kind: 'entry',
    id: index === undefined ? entry.uri : `${entry.uri}#${index}`,
    depth,
    uri: entry.uri,
    name: nameFromUri(entry.uri),
    type: isDir ? 'directory' : 'file',
    expanded: isDir ? expanded : undefined,
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

/**
 * 一个文件的预览页签。行数达到上限时按截断处理——接口不返回总行数，只能由
 * 「读满了」推断出「可能还有」。
 *
 * A preview tab for one file. Reaching the line cap is reported as truncation:
 * the API returns no total, so "the cap was filled" is the only signal that more
 * content may exist.
 */
export function toFileTab(
  entry: FsEntry,
  content: string,
  maxLines: number,
  icon?: ReactNode,
  openParent?: (uri: string) => Promise<void>,
): FilePreviewFileTab {
  const name = nameFromUri(entry.uri);
  return {
    kind: 'file',
    key: entry.uri,
    // 页签显示文件名（含扩展名）与同款类型图标，不显示完整 URI——完整路径
    // 已经由标题行的面包屑给出，页签再放一遍只会挤掉相邻页签。
    //
    // The tab shows the file name (with its extension) and the same type icon, not
    // the full URI: the breadcrumb in the header already carries the path, and
    // repeating it would crowd out the neighbouring tabs.
    title: name,
    icon,
    operations: { openParent },
    source: {
      content,
      fileName: name,
      path: entry.uri,
      truncated: content.split('\n').length >= maxLines,
    },
  };
}

/**
 * 目录的预览页签：两级摘要各自成一级，走 `directory` 页签，而不是文件页签。
 *
 * 文件页签只有正文，目录还要在标题行与正文之间多一层同级切换——这正是
 * `directory` 这一类存在的理由。
 *
 * A preview tab for a directory: each summary level is one level of a `directory`
 * tab rather than a file tab.
 *
 * A file tab is just a body; a directory also needs one more level of sibling
 * switching between its header and body, which is exactly why the `directory` kind
 * exists.
 */
export interface DirectorySummary {
  /** L0 摘要正文。The L0 abstract body. */
  abstract: string;
  /** L1 总览正文。The L1 overview body. */
  overview: string;
  /**
   * 该级摘要的请求失败原因。为 `undefined` 表示请求成功（正文可能是空串，即
   * 「还没生成」）；有值表示「取不到」，与「没生成」是两件事，必须让用户分得清。
   *
   * Why that level's request failed. `undefined` means the request succeeded (the body
   * may be empty, meaning "not generated yet"); a value means "could not load", which
   * is a different situation the user has to be able to tell apart.
   */
  abstractError?: string;
  overviewError?: string;
}

export function toDirectoryTab(
  uri: string,
  summary: DirectorySummary,
  labels: { l0: string; l1: string },
  openParent?: (uri: string) => Promise<void>,
  icon?: ReactNode,
): FilePreviewDirectoryTab {
  const name = nameFromUri(uri);
  // 取不到的那一级显示失败原因而不是空正文：空正文会被读成「还没生成」，而两级
  // 摘要各自可能独立失败（后端出错、权限不足），这两种情况处置完全不同。
  //
  // 判据是「有没有这个字段」而不是「它是否为真」：服务端可以返回空字符串作为
  // 错误消息，用真假值判断会把一次失败重新变成空正文。消息为空时给一句可见的兜底
  // 文案，同样是为了不让失败看起来像「还没生成」。
  //
  // A level that could not be loaded shows the reason instead of an empty body: an
  // empty body reads as "not generated yet", while the two levels can fail
  // independently (backend error, missing permission) and the two call for different
  // actions.
  //
  // The test is "the field is present", not "it is truthy": a server may report an
  // empty message, and a truthiness check would turn that failure back into an empty
  // body. An empty message falls back to a visible line for the same reason — a
  // failure must not look like "not generated yet".
  const levelBody = (body: string, failure?: string) =>
    failure === undefined
      ? body
      : `> ${failure || 'The summary could not be loaded.'}`;
  return {
    kind: 'directory',
    key: uri,
    // 目录页签显示末级目录名与文件夹图标，与树上的那一行一致。
    // A directory tab shows the last path segment and the folder icon, matching
    // the row in the tree.
    title: name,
    icon,
    levels: [
      {
        key: 'l0',
        label: labels.l0,
        source: {
          content: levelBody(summary.abstract, summary.abstractError),
          fileName: `${name}.md`,
          path: uri,
          fileType: 'markdown',
        },
      },
      {
        key: 'l1',
        label: labels.l1,
        source: {
          content: levelBody(summary.overview, summary.overviewError),
          fileName: `${name}.md`,
          path: uri,
          fileType: 'markdown',
        },
      },
    ],
    operations: { openParent },
  };
}

/** 把目录状态展开成按渲染顺序排列的行，只展开已打开的分支。 */
export function flattenTree(
  uri: string,
  depth: number,
  dirs: Record<string, DirState>,
  expanded: ReadonlySet<string>,
): FileTreeRow[] {
  const dir = dirs[uri];
  if (!dir) return [];
  const rows: FileTreeRow[] = [];

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

/** 请求被取消时不需要写回状态。A cancelled request has no state to write. */
export function isAbort(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: unknown }).name === 'AbortError'
  );
}

/** 把任意抛出物归一成 Error。Normalises a thrown value into an Error. */
export function toError(cause: unknown): Error {
  return cause instanceof Error ? cause : new Error(String(cause));
}
