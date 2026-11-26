import type { FileBrowserDashboardLabels } from './types.js';
import { mergeLabels } from '../../shared/locale.js';
const en = {
  experienceTitle: 'Experience dashboard',
  wikiTitle: 'Dashboard',
  openExperience: 'Open experience dashboard',
  openWiki: 'Open Wiki dashboard',
  wikiConflicts: 'View pending Wiki conflicts',
  wikiConflictCount: (count: number) => `${count} unresolved conflicts`,
  wikiConflictFileCount: (count: number) =>
    `${count > 99 ? '99+' : count} files with unresolved conflicts`,
  failed: 'Could not load dashboard information',
};
const zh: typeof en = {
  experienceTitle: '经验看板',
  wikiTitle: '看板',
  openExperience: '打开经验看板',
  openWiki: '打开 Wiki 看板',
  wikiConflicts: '查看 Wiki 待确认冲突',
  wikiConflictCount: (count) => `${count} 条待解决冲突`,
  wikiConflictFileCount: (count) =>
    `${count > 99 ? '99+' : count} 个存在待解决冲突的文件`,
  failed: '看板信息加载失败',
};
export const dashboardLabels = (
  locale?: string,
  labels?: FileBrowserDashboardLabels,
) => mergeLabels(locale === 'zh-CN' ? zh : en, labels);
