import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import { createDashboardTab } from './dashboard-tab.js';
import type { FileBrowserDashboardLabels } from './types.js';
import { readCompileTargets } from './dashboard-data.js';
import { type ReactNode, useCallback, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { readDashboardHint } from './dashboard-resource.js';
import { isOvClientError } from '../../client/errors.js';
import type { FileTreeRow } from '../../components/file-tree/index.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { useWikiConflictIndicators } from './wiki-conflict-indicators.js';
import {
  type FileBrowserDashboard,
  resolveDashboard,
} from './dashboard-model.js';

export function useBrowserDashboards({
  rows,
  metadataRows,
  uri,
  rootUri,
  identifyUri,
  generation,
  locale,
  requestOptions,
  onOpenUri,
  dashboardKinds,
  experienceDataSource,
  renderDashboard,
  dashboardLabels,
}: {
  rows: readonly FileTreeRow[];
  metadataRows: readonly FileTreeRow[];
  uri: string | null;
  rootUri: string;
  identifyUri: (uri: string) => string;
  generation: number;
  dashboardLabels?: FileBrowserDashboardLabels;
  dashboardKinds?: readonly FileBrowserDashboard['kind'][];
  experienceDataSource?: FileBrowserExperienceDataSource;
  renderDashboard?: (dashboard: FileBrowserDashboard) => ReactNode;
  locale?: 'en-US' | 'zh-CN';
  requestOptions?: OvRequestOptions;
  onOpenUri: (uri: string) => Promise<void>;
}) {
  const [opened, setOpened] = useState<string | null>(null);
  const [conflictRevision, setConflictRevision] = useState(0);
  const refreshConflicts = useCallback(
    () => setConflictRevision((value) => value + 1),
    [],
  );
  const [previousUri, setPreviousUri] = useState(uri);
  if (previousUri !== uri) {
    setPreviousUri(uri);
    if (opened !== uri) setOpened(null);
  }
  const load = useCallback(
    (client: OvClient, options: OvRequestOptions) =>
      readCompileTargets(client, rootUri, options),
    [rootUri],
  );
  const wikiEnabled =
    dashboardKinds === undefined || dashboardKinds.includes('wiki');
  const loadHint = useCallback(
    async (client: OvClient, options: OvRequestOptions) =>
      uri ? readDashboardHint(client, uri, options) : undefined,
    [uri],
  );
  const hint = useDashboardQuery(
    loadHint,
    requestOptions,
    wikiEnabled && Boolean(uri),
    generation,
  );
  const canonicalUri = hint.data?.uri ?? uri;
  const knownRows: readonly FileTreeRow[] =
    canonicalUri && hint.data?.tags.includes('compile_target=true')
      ? [
          ...metadataRows,
          {
            kind: 'entry',
            uri: canonicalUri,
            id: canonicalUri,
            name: canonicalUri,
            depth: 0,
            type: 'directory',
            tags: hint.data.tags,
          } as FileTreeRow,
        ]
      : metadataRows;
  // Tree tags and scoped attrs advertise boards without querying Compile.
  // The registry only enriches an explicitly opened Wiki board.
  const wantsWiki =
    wikiEnabled &&
    uri !== null &&
    opened === uri &&
    resolveDashboard(canonicalUri ?? uri, knownRows, [])?.kind !== 'experience';
  const catalog = useDashboardQuery(
    load,
    requestOptions,
    wantsWiki,
    generation,
  );
  // An old server without Compile has no Wiki feature. Other failures stay visible.
  const unsupported =
    isOvClientError(catalog.error) && catalog.error.statusCode === 404;
  const targets = catalog.data ?? [];
  const resolve = (target: string) => {
    const match = resolveDashboard(
      target === uri ? (canonicalUri ?? target) : target,
      knownRows,
      targets,
    );
    return match ? { ...match, uri: target } : undefined;
  };
  const dashboards = new Map(
    rows
      .filter((row) => row.kind === 'entry')
      .filter((row) => !row.disabled && row.selectable !== false)
      .flatMap((row) => {
        const dashboard = resolve(row.uri);
        return dashboard &&
          (dashboardKinds === undefined ||
            dashboardKinds.includes(dashboard.kind))
          ? [[row.uri, dashboard] as const]
          : [];
      }),
  );
  const resolved = uri ? resolve(uri) : undefined;
  const current =
    resolved &&
    (dashboardKinds === undefined || dashboardKinds.includes(resolved.kind))
      ? resolved
      : undefined;
  const conflictStatus = useWikiConflictIndicators(
    rows,
    dashboards,
    current,
    identifyUri,
    generation + conflictRevision,
    requestOptions,
  );
  for (const dashboard of dashboards.values())
    dashboard.pendingConflicts = conflictStatus(dashboard);
  if (current) current.pendingConflicts = conflictStatus(current);
  const open = useCallback(
    (target: string) => {
      setOpened(target);
      void onOpenUri(target);
    },
    [onOpenUri],
  );
  const close = useCallback(() => setOpened(null), []);
  const tab = current
    ? createDashboardTab(current, {
        locale,
        labels: dashboardLabels,
        requestOptions,
        generation,
        conflictRevision,
        isFile: knownRows.some(
          (row) =>
            row.kind === 'entry' &&
            row.uri === canonicalUri &&
            row.type === 'file',
        ),
        experienceDataSource,
        renderDashboard,
        onOpenUri: (target) => {
          close();
          void onOpenUri(target);
        },
        onOpenDashboard: open,
      })
    : undefined;
  return {
    isWiki: resolved?.kind === 'wiki',
    contentPending: Boolean(
      uri && /\.md$/i.test(uri) && !resolved && hint.loading,
    ),
    dashboards,
    current,
    tab,
    isOpen: uri !== null && opened === uri && Boolean(current),
    open,
    close,
    refreshConflicts,
    error: unsupported ? null : catalog.error,
    retry: catalog.retry,
  };
}
