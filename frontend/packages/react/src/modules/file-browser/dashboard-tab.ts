import type { ExperienceDashboardProps } from '../../components/experience-dashboard/index.js';
import { createElement, type ReactNode } from 'react';
import type { OvRequestOptions } from '../../client/types.js';
import type { FilePreviewTab } from '../../components/file-preview/index.js';
import { ExperienceBoardIcon, WikiBoardIcon } from '../../icons/index.js';
import { dashboardLabels } from './dashboard-labels.js';
import { dashboardKey, type FileBrowserDashboard } from './dashboard-model.js';
import { ExperienceDashboardPane } from './experience-dashboard-pane.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import type { FileBrowserDashboardLabels } from './types.js';
import { WikiDashboardView } from './wiki-dashboard-pane.js';
import type { useWikiDashboard } from './use-wiki-dashboard.js';

export function createDashboardTab(
  dashboard: FileBrowserDashboard,
  {
    locale,
    labels,
    requestOptions,
    generation,
    isFile,
    wikiQuery,
    experienceDataSource,
    experienceDashboardSlots,
    renderDashboard,
    onOpenUri,
    onOpenDashboard,
  }: {
    locale?: 'en-US' | 'zh-CN';
    labels?: FileBrowserDashboardLabels;
    requestOptions?: OvRequestOptions;
    generation: number;
    isFile: boolean;
    wikiQuery: ReturnType<typeof useWikiDashboard>;
    experienceDataSource?: FileBrowserExperienceDataSource;
    experienceDashboardSlots?: ExperienceDashboardProps['slots'];
    renderDashboard?: (dashboard: FileBrowserDashboard) => ReactNode;
    onOpenUri: (uri: string) => void;
    onOpenDashboard: (uri: string) => void;
  },
): FilePreviewTab {
  const { uri } = dashboard;
  const text = dashboardLabels(locale, labels);
  const experience = dashboard.kind === 'experience';
  const result = wikiQuery.data;
  const count =
    result?.kind === 'file'
      ? result.data.conflicts.filter((conflict) => conflict.state === 'unfixed')
          .length
      : result?.data.conflictFileCount;
  const common = { uri, locale, onOpenUri };
  return {
    key: dashboardKey(uri),
    title: experience ? text.experienceTitle : text.wikiTitle,
    badge:
      !experience && count !== undefined && count > 0
        ? {
            count,
            label:
              result?.kind === 'file'
                ? text.wikiConflictCount(count)
                : text.wikiConflictFileCount(count),
          }
        : undefined,
    icon: createElement(experience ? ExperienceBoardIcon : WikiBoardIcon, {
      width: 14,
      height: 14,
    }),
    kind: 'content',
    hideWhileEditing: true,
    content:
      renderDashboard?.(dashboard) ??
      (experience
        ? createElement(ExperienceDashboardPane, {
            ...common,
            requestOptions,
            dataSource: experienceDataSource,
            slots: experienceDashboardSlots,
            refreshGeneration: generation,
          })
        : createElement(WikiDashboardView, {
            ...common,
            kind: isFile ? 'file' : 'directory',
            query: wikiQuery,
            onOpenDashboard,
          })),
  };
}
