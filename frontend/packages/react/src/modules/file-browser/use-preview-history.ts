import { useCallback, useMemo, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import {
  readHistoricalText,
  readPreviewHistory,
} from './preview-history-data.js';
import { diffLines } from './preview-diff.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import type { PreviewHistoryProps } from './preview-history.js';
import type { PreviewHistoryEntry } from './preview-workflow-types.js';

export function usePreviewHistory(props: PreviewHistoryProps) {
  const { uri, versioned, showContent, requestOptions } = props;
  const query = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        readPreviewHistory(client, uri, versioned, options),
      [uri, versioned],
    ),
    requestOptions,
  );
  const [selectedId, setSelectedId] = useState<string>();
  const [baseId, setBaseId] = useState<string>();
  const [compare, setCompare] = useState(props.directory);
  const [deletedIds, setDeletedIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const loadRevision = useCallback(
    async (
      client: OvClient,
      entry: PreviewHistoryEntry,
      options: OvRequestOptions,
    ) => {
      const data = await readHistoricalText(client, entry, options, versioned);
      if (data === null && !options.signal?.aborted) {
        setDeletedIds((known) =>
          known.has(entry.id) ? known : new Set([...known, entry.id]),
        );
      }
      return data;
    },
    [versioned],
  );
  const entries = query.data;
  const selected = showContent
    ? (entries?.find((entry) => entry.id === selectedId) ?? entries?.[0])
    : undefined;
  const previous =
    entries?.[
      (entries?.findIndex((entry) => entry.id === selected?.id) ?? -1) + 1
    ];
  const base =
    entries?.find(
      (entry) => entry.id === baseId && entry.id !== selected?.id,
    ) ?? previous;
  const content = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        loadRevision(client, selected!, options),
      [loadRevision, selected],
    ),
    requestOptions,
    Boolean(selected),
  );
  const current = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        loadRevision(client, base!, options),
      [loadRevision, base],
    ),
    requestOptions,
    Boolean(base) && compare,
  );
  const addedFile = Boolean(selected && !base);
  const canCompare = Boolean(base) || addedFile;
  const changes = useMemo(
    () =>
      compare &&
      canCompare &&
      content.data !== undefined &&
      (!base || current.data !== undefined)
        ? diffLines(current.data ?? '', content.data ?? '')
        : undefined,
    [compare, base, canCompare, content.data, current.data],
  );
  return {
    query,
    deletedIds,
    selected,
    base,
    content,
    current,
    changes,
    compare: compare && canCompare,
    canCompare,
    requestedCompare: compare,
    setCompare,
    identical: content.data !== undefined && content.data === current.data,
    select: (id: string) => {
      setSelectedId(id);
      setBaseId(undefined);
    },
    setBaseId,
  };
}
