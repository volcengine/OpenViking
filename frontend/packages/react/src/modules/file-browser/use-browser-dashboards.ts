import type { ExperienceDashboardProps } from '../../components/experience-dashboard/index.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import { createDashboardTab } from './dashboard-tab.js';
import type { FileBrowserDashboardLabels } from './types.js';
import { readCompileTargets } from './dashboard-data.js';
import { type ReactNode, useCallback, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isOvClientError } from '../../client/errors.js';
import type { FileTreeRow } from '../../components/file-tree/index.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { useWikiConflictIndicators } from './wiki-conflict-indicators.js';
import {
  useWikiConflictIndex,
  useDashboardHint,
} from './use-dashboard-metadata.js';
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
  experienceDashboardSlots,
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
  experienceDashboardSlots?: ExperienceDashboardProps['slots'];
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
  const { hint, canonicalUri, knownRows, isFile } = useDashboardHint({
    uri,
    rows: metadataRows.filter((row) => row.kind !== 'entry' || !row.disabled),
    enabled: wikiEnabled,
    generation,
    requestOptions,
  });
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
  const resolve = (target: string, indexedConflict?: boolean) => {
    const match = resolveDashboard(
      target === uri ? (canonicalUri ?? target) : target,
      knownRows,
      targets,
      indexedConflict,
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
  const searchConflicts = useWikiConflictIndicators({
    rows,
    knownRows,
    dashboardRows: dashboards,
    current,
    targets,
    rootUri,
    identifyUri,
    generation: generation + conflictRevision,
    requestOptions,
    enabled: wikiEnabled,
  });
  const conflicts = useWikiConflictIndex({
    rows: [],
    targets: searchConflicts.listingRoots.map((to) => ({ to })),
    rootUri,
    identifyUri,
    enabled: wikiEnabled,
    generation: generation + conflictRevision,
    requestOptions,
  });
  const markConflicts = (dashboard: FileBrowserDashboard) => {
    if (dashboard.kind !== 'wiki') return;
    const presence = searchConflicts.presence(dashboard.uri);
    const indexed = conflicts.presence(identifyUri(dashboard.uri));
    dashboard.pendingConflicts =
      presence ??
      (indexed === undefined
        ? dashboard.pendingConflicts
        : resolve(dashboard.uri, indexed)?.pendingConflicts);
  };
  for (const dashboard of dashboards.values()) markConflicts(dashboard);
  if (current) markConflicts(current);
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
        isFile,
        experienceDataSource,
        experienceDashboardSlots,
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
    error: (unsupported ? null : catalog.error) ?? conflicts.error,
    retry: () => {
      catalog.retry();
      refreshConflicts();
    },
  };
}
