import { normalizeResourceUri } from './fs-uri.js';
import type { FileBrowserDashboard } from './dashboard-model.js';
import { isExperienceFile } from './experience-data.js';

export const isSystemUri = (uri: string) =>
  /^viking:\/\/system(?:\/|$)/i.test(normalizeResourceUri(uri));
export const isSummaryUri = (uri: string) =>
  /\/(?:\.abstract|\.overview)\.md$/i.test(normalizeResourceUri(uri));
export const isEditableText = (uri: string) =>
  /\.(?:md|json|jsonl|txt|yaml|yml)$/i.test(normalizeResourceUri(uri)) &&
  !isSystemUri(uri) &&
  !isSummaryUri(uri);
export const supportsFileVersions = (uri: string) =>
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
  const fileHistory = isFile && supportsFileVersions(uri);
  return {
    system,
    edit: isFile && !system && !summary && !experience && canWrite,
    viewSwitch: !system || isFile,
    copyContent: !system || isFile,
    download: isFile,
    history:
      !system &&
      (fileHistory ||
        experience ||
        (wiki && !summary && !/\/\.relations\.json$/.test(uri))),
    fileHistory,
    versionedFile: wiki && isFile && !summary,
    importPack: !isFile && !system && canWrite && supportsPack(uri),
    exportPack: !system && supportsPack(uri),
    delete: isFile && !system && !summary && uri !== rootUri && canManage,
    configure: !isFile && !system && wiki && dashboard.isRoot === true,
  };
}
