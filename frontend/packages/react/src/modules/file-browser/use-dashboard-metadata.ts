import { useCallback } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/types.js';
import { within } from './dashboard-model.js';
import {
  isSystemUri,
  normalizeResourceUri,
} from '../../shared/resource-uri.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { readWikiConflictIndex } from './wiki-conflict-index.js';
import { findCompileTarget, readDashboardHint } from './dashboard-resource.js';

export function useDashboardHint({
  uri,
  rows,
  enabled,
  generation,
  requestOptions,
}: {
  uri: string | null;
  rows: readonly FileTreeRow[];
  enabled: boolean;
  generation: number;
  requestOptions?: OvRequestOptions;
}) {
  const load = useCallback(
    async (client: OvClient, options: OvRequestOptions) =>
      uri ? readDashboardHint(client, uri, options) : undefined,
    [uri],
  );
  const hint = useDashboardQuery(
    load,
    requestOptions,
    enabled && Boolean(uri),
    generation,
  );
  const canonicalUri = hint.data?.uri ?? uri;
  const knownRows: readonly FileTreeRow[] =
    canonicalUri && hint.data?.tags.includes('compile_target=true')
      ? [
          ...rows,
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
      : rows;
  const isFile = knownRows.some(
    (row) =>
      row.kind === 'entry' && row.uri === canonicalUri && row.type === 'file',
  );
  return { hint, canonicalUri, knownRows, isFile };
}

// Run provenance discovery independently of the preview body and dashboard.
export function useCompileTarget(
  uri: string | null,
  generation: number,
  requestOptions?: OvRequestOptions,
) {
  const load = useCallback(
    (client: OvClient, options: OvRequestOptions) =>
      uri
        ? findCompileTarget(client, uri, options)
        : Promise.resolve(undefined),
    [uri],
  );
  return useDashboardQuery(
    load,
    requestOptions,
    uri !== null && !isSystemUri(uri),
    generation,
  ).data;
}

export function useWikiConflictIndex({
  rows,
  targets,
  rootUri,
  identifyUri,
  enabled,
  generation,
  requestOptions,
}: {
  rows: readonly FileTreeRow[];
  targets: readonly { to: string }[];
  rootUri: string;
  identifyUri: (uri: string) => string;
  enabled: boolean;
  generation: number;
  requestOptions?: OvRequestOptions;
}) {
  const scope = identifyUri(rootUri);
  const roots = [
    ...new Set(
      [
        ...targets.map((target) => target.to),
        ...rows
          .filter(
            (row): row is FileTreeEntryRow & { tags?: readonly string[] } =>
              row.kind === 'entry',
          )
          .filter((row) => row.tags?.includes('compile_target=true'))
          .map((row) => row.uri),
      ]
        .map((uri) => normalizeResourceUri(identifyUri(uri)))
        .filter((uri) => within(uri, scope) || within(scope, uri))
        .map((uri) => (within(scope, uri) ? scope : uri)),
    ),
  ];
  const key = JSON.stringify(
    roots
      .filter(
        (uri) => !roots.some((other) => other !== uri && within(uri, other)),
      )
      .sort(),
  );
  const load = useCallback(
    (client: OvClient, options: OvRequestOptions) =>
      readWikiConflictIndex(client, JSON.parse(key) as string[], options),
    [key],
  );
  const query = useDashboardQuery(
    load,
    requestOptions,
    enabled && key !== '[]',
    generation,
  );
  return {
    ...query,
    presence: (uri: string) =>
      query.data?.roots.some((root) => within(uri, root))
        ? query.data.paths.has(uri)
        : undefined,
  };
}
