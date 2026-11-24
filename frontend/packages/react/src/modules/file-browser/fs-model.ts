import type {
  FilePreviewDirectoryTab,
  FilePreviewFileTab,
} from '../../components/file-preview/index.js';
import type { ReactNode } from 'react';

import { nameFromUri } from '../../shared/directory-tree/rows.js';
import { directoryIcon } from './fs-icon.js';
import { decodePathLabel } from '../../shared/path-label.js';
import type { FsEntry } from '../../shared/directory-tree/types.js';
import type { DirectorySummaryLabels } from './file-browser-labels.js';

/**
 * 一个文件的预览页签。行数达到上限时按截断处理——接口不返回总行数，只能由
 * 「读满了」推断出「可能还有」。
 *
 * 调用方自己切过行时用 `truncated` 说明结果，因为「正文行数等于上限」既可能是
 * 正好读完，也可能是切掉了一部分，只有切的人知道是哪一种。
 *
 * A preview tab for one file. Reaching the line cap is reported as truncation:
 * the API returns no total, so "the cap was filled" is the only signal that more
 * content may exist.
 *
 * A caller that sliced the lines itself states the outcome through `truncated`:
 * "the body has exactly the cap" can mean the file ended there or that the rest was
 * cut, and only the caller knows which.
 */
export function toFileTab(
  entry: FsEntry,
  content: string,
  maxLines: number,
  icon?: ReactNode,
  openParent?: (uri: string) => Promise<void>,
  truncated = content.split('\n').length >= maxLines,
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
    title: decodePathLabel(name),
    icon,
    operations: { openParent },
    source: {
      content,
      fileName: name,
      path: entry.uri,
      truncated,
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

type DirectorySummaryLevel = FilePreviewDirectoryTab['levels'][number] & {
  hasSummary: boolean;
};

export function toDirectoryTab(
  uri: string,
  summary: DirectorySummary,
  labels: DirectorySummaryLabels,
  openParent?: (uri: string) => Promise<void>,
  navigation?: Pick<
    FilePreviewDirectoryTab,
    'resolveImage' | 'activeLevelKey' | 'onActiveLevelChange'
  >,
): Omit<FilePreviewDirectoryTab, 'levels'> & {
  levels: readonly [DirectorySummaryLevel, DirectorySummaryLevel];
} {
  const name = nameFromUri(uri);
  // 取不到的那一级显示失败原因而不是空正文：空正文会被读成「还没生成」，而两级
  // 摘要各自可能独立失败（后端出错、权限不足），这两种情况处置完全不同。
  //
  // 判据是「有没有这个字段」而不是「它是否为真」：服务端可以返回空字符串作为
  // 错误消息，用真假值判断会把一次失败重新变成空正文。消息为空时用 `labels.failure`
  // 兜底——兜底文案同样来自可替换文案，不能在这里写死某一种语言。
  //
  // A level that could not be loaded shows the reason instead of an empty body: an
  // empty body reads as "not generated yet", while the two levels can fail
  // independently (backend error, missing permission) and the two call for different
  // actions.
  //
  // The test is "the field is present", not "it is truthy": a server may report an
  // empty message, and a truthiness check would turn that failure back into an empty
  // body. An empty message falls back to `labels.failure` — that fallback is replaceable
  // copy too, so no single language is hard-coded here.
  const levelBody = (body: string, failure?: string) =>
    failure === undefined ? body : `> ${failure || labels.failure}`;
  return {
    kind: 'directory',
    key: uri,
    // 目录页签显示末级目录名与文件夹图标，与树上的那一行一致。
    // A directory tab shows the last path segment and the folder icon, matching
    // the row in the tree.
    title: decodePathLabel(name),
    icon: directoryIcon(),
    ...navigation,
    levels: [
      {
        key: 'l0',
        hasSummary:
          Boolean(summary.abstract) && summary.abstractError === undefined,
        label: labels.l0,
        emptyLabel: labels.l0Empty,
        source: {
          content: levelBody(summary.abstract, summary.abstractError),
          fileName: `${name}.md`,
          path: uri,
          fileType: 'markdown',
        },
      },
      {
        key: 'l1',
        hasSummary:
          Boolean(summary.overview) && summary.overviewError === undefined,
        label: labels.l1,
        emptyLabel: labels.l1Empty,
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
