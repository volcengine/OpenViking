import { createDashboardTab } from './dashboard-tab.js';
import type { FileBrowserDashboard } from './dashboard-model.js';
import { useWikiDashboard } from './use-wiki-dashboard.js';

/** One query supplies the Wiki pane and its badge, including while content is selected. */
export function useDashboardTab(
  dashboard: FileBrowserDashboard | undefined,
  options: Omit<Parameters<typeof createDashboardTab>[1], 'wikiQuery'> & {
    conflictRevision: number;
  },
) {
  const rendered = dashboard && options.renderDashboard?.(dashboard);
  const wikiQuery = useWikiDashboard({
    uri: dashboard?.uri ?? '',
    enabled: dashboard?.kind === 'wiki',
    requestOptions: options.requestOptions,
    refreshGeneration: options.generation + options.conflictRevision,
  });
  return dashboard
    ? createDashboardTab(dashboard, {
        ...options,
        renderDashboard: () => rendered,
        wikiQuery,
      })
    : undefined;
}
