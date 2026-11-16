import { createElement, type ReactNode } from 'react';
import type { OvRequestOptions } from '../../client/types.js';
import type { FilePreviewTab } from '../../components/file-preview/index.js';
import { ExperienceBoardIcon, WikiBoardIcon } from '../../icons/index.js';
import { dashboardLabels } from './dashboard-labels.js';
import { dashboardKey, type FileBrowserDashboard } from './dashboard-model.js';
import { ExperienceDashboardPane } from './experience-dashboard-pane.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import type { FileBrowserDashboardLabels } from './types.js';
import { WikiDashboardPane } from './wiki-dashboard-pane.js';

export function createDashboardTab(
  dashboard: FileBrowserDashboard,
  {
    locale,
    labels,
    requestOptions,
    generation,
    conflictRevision,
    isFile,
    experienceDataSource,
    renderDashboard,
    onOpenUri,
    onOpenDashboard,
  }: {
    locale?: 'en-US' | 'zh-CN';
    labels?: FileBrowserDashboardLabels;
    requestOptions?: OvRequestOptions;
    generation: number;
    conflictRevision: number;
    isFile: boolean;
    experienceDataSource?: FileBrowserExperienceDataSource;
    renderDashboard?: (dashboard: FileBrowserDashboard) => ReactNode;
    onOpenUri: (uri: string) => void;
    onOpenDashboard: (uri: string) => void;
  },
): FilePreviewTab {
  const { uri } = dashboard;
  const text = dashboardLabels(locale, labels);
  const experience = dashboard.kind === 'experience';
  const common = { uri, locale, requestOptions, onOpenUri };
  return {
    key: dashboardKey(uri),
    title: experience ? text.experienceTitle : text.wikiTitle,
    icon: createElement(experience ? ExperienceBoardIcon : WikiBoardIcon, {
      width: 14,
      height: 14,
    }),
    kind: 'content',
    content:
      renderDashboard?.(dashboard) ??
      (experience
        ? createElement(ExperienceDashboardPane, {
            ...common,
            dataSource: experienceDataSource,
            refreshGeneration: generation,
          })
        : createElement(WikiDashboardPane, {
            ...common,
            kind: isFile ? 'file' : 'directory',
            refreshGeneration: generation + conflictRevision,
            onOpenDashboard,
          })),
  };
}
