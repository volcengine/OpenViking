import {
  isSystemUri,
  normalizeResourceUri,
} from '../../shared/resource-uri.js';
import type { FileBrowserDashboard } from './dashboard-model.js';
import { isExperienceFile } from './experience-data.js';

export const isSummaryUri = (uri: string) =>
  /\/(?:\.abstract|\.overview)\.md$/i.test(normalizeResourceUri(uri));
export const isEditableText = (uri: string) =>
  /\.(?:md|json|jsonl|txt|yaml|yml)$/i.test(normalizeResourceUri(uri)) &&
  !isSystemUri(uri) &&
  !isSummaryUri(uri);
/**
 * Wiki 目录历史里可列出的文件：Wiki 产物是 Markdown 与常见文本，二进制和摘要
 * 派生文件不在其列。
 *
 * Files a Wiki directory history can list: Wiki outputs are Markdown and ordinary
 * text, so binaries and derived summaries are excluded.
 */
export const supportsWikiHistoryFile = (uri: string) =>
  isEditableText(uri) &&
  !/\/\.relations\.json$/i.test(normalizeResourceUri(uri));

/** Namespace scope mirrors ovpack/policy.py; import targets are directories. */
export const supportsPack = (value: string) => {
  const uri = normalizeResourceUri(value);
  return (
    /^viking:\/\/(?:resources|user|~)(?:\/|$)/.test(uri) &&
    !/\/\.relations\.json$/.test(uri) &&
    !/^viking:\/\/resources\/\.watch_tasks\.json(?:\.bak|\.tmp)?$/.test(uri)
  );
};

export function previewPolicy({
  uri,
  isFile,
  rootUri,
  canWrite,
  canManage,
  dashboard,
}: {
  uri: string;
  isFile: boolean;
  rootUri: string;
  canWrite: boolean;
  canManage: boolean;
  dashboard?: FileBrowserDashboard;
}) {
  uri = normalizeResourceUri(uri);
  rootUri = normalizeResourceUri(rootUri);
  const system = isSystemUri(uri);
  const summary = isSummaryUri(uri);
  const experience = isFile && isExperienceFile(uri);
  const wiki = dashboard?.kind === 'wiki';
  // 版本历史只给经验文件与 Wiki 节点，与内部版一致：普通文本文件既没有用户可见
  // 的历史语义，也没有保存快照的入口。经验目录本身走经验看板，不在此列。
  //
  // Version history is for experience files and Wiki nodes only, matching the
  // internal console: an ordinary text file has no user-facing history semantics
  // and no snapshot entry. An experience directory has its own dashboard instead.
  const fileHistory = isFile && experience;
  return {
    system,
    edit: isFile && !system && !summary && !experience && canWrite,
    viewSwitch: !system || isFile,
    copyContent: !system || isFile,
    download: isFile,
    history:
      !system &&
      (fileHistory || (wiki && !summary && !/\/\.relations\.json$/.test(uri))),
    fileHistory,
    versionedFile: wiki && isFile && !summary,
    importPack: !isFile && !system && canWrite && supportsPack(uri),
    exportPack: !isFile && !system && supportsPack(uri),
    delete: isFile && !system && !summary && uri !== rootUri && canManage,
    configure: !isFile && !system && wiki && dashboard.isRoot === true,
  };
}
