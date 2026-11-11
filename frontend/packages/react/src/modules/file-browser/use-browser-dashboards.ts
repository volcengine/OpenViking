import { dashboardLabels as resolveDashboardLabels } from './dashboard-labels.js';
import type { FileBrowserDashboardLabels } from './types.js';
import { readCompileTargets } from './dashboard-data.js';
import { type ReactNode, createElement, useCallback, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { readDashboardHint } from './dashboard-resource.js';
import { isOvClientError } from '../../client/errors.js';
import type { FileTreeRow } from '../../components/file-tree/index.js';
import type { FilePreviewTab } from '../../components/file-preview/index.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { ExperienceDashboardPane } from './experience-dashboard-pane.js';
import { WikiDashboardPane } from './wiki-dashboard-pane.js';
import { ExperienceBoardIcon, WikiBoardIcon } from '../../icons/index.js';
import {
  type FileBrowserDashboard,
  resolveDashboard,
  dashboardKey,
} from './dashboard-model.js';

export function useBrowserDashboards({
  rows,
  metadataRows,
  uri,
  rootUri,
  generation,
  locale,
  requestOptions,
  onOpenUri,
  dashboardKinds,
  renderDashboard,
  dashboardLabels,
}: {
  rows: readonly FileTreeRow[];
  metadataRows: readonly FileTreeRow[];
  uri: string | null;
  rootUri: string;
  generation: number;
  dashboardLabels?: FileBrowserDashboardLabels;
  dashboardKinds?: readonly FileBrowserDashboard['kind'][];
  renderDashboard?: (dashboard: FileBrowserDashboard) => ReactNode;
  locale?: 'en-US' | 'zh-CN';
  requestOptions?: OvRequestOptions;
  onOpenUri: (uri: string) => Promise<void>;
}) {
  const [opened, setOpened] = useState<string | null>(null);
  const load = useCallback(
    (client: OvClient, options: OvRequestOptions) =>
      readCompileTargets(client, rootUri, options),
    [rootUri],
  );
  const wikiEnabled =
    dashboardKinds === undefined || dashboardKinds.includes('wiki');
  const catalog = useDashboardQuery(
    load,
    requestOptions,
    wikiEnabled,
    generation,
  );
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
  const open = useCallback(
    (target: string) => {
      setOpened(target);
      void onOpenUri(target);
    },
    [onOpenUri],
  );
  const close = useCallback(() => setOpened(null), []);
  const text = resolveDashboardLabels(locale, dashboardLabels);
  const title =
    current?.kind === 'experience' ? text.experienceTitle : text.wikiTitle;
  const tab: FilePreviewTab | undefined =
    current && uri
      ? {
          key: dashboardKey(uri),
          title,
          icon: createElement(
            current.kind === 'experience' ? ExperienceBoardIcon : WikiBoardIcon,
            { width: 14, height: 14 },
          ),
          kind: 'content',
          content:
            renderDashboard?.(current) ??
            (current.kind === 'experience'
              ? createElement(ExperienceDashboardPane, {
                  uri,
                  locale,
                  requestOptions,
                  refreshGeneration: generation,
                  onOpenUri: (target: string) => {
                    setOpened(null);
                    void onOpenUri(target);
                  },
                })
              : createElement(WikiDashboardPane, {
                  uri,
                  kind: knownRows.some(
                    (row) =>
                      row.kind === 'entry' &&
                      row.uri === canonicalUri &&
                      row.type === 'file',
                  )
                    ? 'file'
                    : 'directory',
                  locale,
                  requestOptions,
                  refreshGeneration: generation,
                  onOpenUri: (target: string) => {
                    setOpened(null);
                    void onOpenUri(target);
                  },
                  onOpenDashboard: open,
                })),
        }
      : undefined;
  return {
    dashboards,
    current,
    tab,
    isOpen: uri !== null && opened === uri && Boolean(current),
    open,
    close,
    error: unsupported ? null : catalog.error,
    retry: catalog.retry,
  };
}
