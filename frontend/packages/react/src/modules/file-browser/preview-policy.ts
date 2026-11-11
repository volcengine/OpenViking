import type { FileBrowserDashboard } from './dashboard-model.js';
import { isExperienceFile } from './experience-data.js';

export const isSystemUri = (uri: string) =>
  /^viking:\/\/system(?:\/|$)/i.test(uri);
export const isSummaryUri = (uri: string) =>
  /\/(?:\.abstract|\.overview)\.md$/i.test(uri);

/** Namespace scope mirrors ovpack/policy.py; import targets are directories. */
export const supportsPack = (uri: string) =>
  /^viking:\/\/(?:resources|user|~)(?:\/|$)/.test(uri) &&
  !/\/\.relations\.json$/.test(uri) &&
  !/^viking:\/\/resources\/\.watch_tasks\.json(?:\.bak|\.tmp)?$/.test(uri);

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
  const system = isSystemUri(uri);
  const summary = isSummaryUri(uri);
  const experience = isFile && isExperienceFile(uri);
  const wiki = dashboard?.kind === 'wiki';
  return {
    system,
    edit: isFile && !system && !summary && !experience && canWrite,
    viewSwitch: !system || isFile,
    copyContent: !system || isFile,
    download: isFile,
    history:
      !system &&
      (experience || (wiki && !summary && !/\/\.relations\.json$/.test(uri))),
    versionedFile: wiki && isFile && !summary,
    importPack: !isFile && !system && canWrite && supportsPack(uri),
    exportPack: !system && supportsPack(uri),
    delete: isFile && !system && !summary && uri !== rootUri && canManage,
    configure: !isFile && !system && wiki && dashboard.isRoot === true,
  };
}
