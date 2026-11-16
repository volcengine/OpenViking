import { useCallback, useEffect, useMemo, useState } from 'react';
import type {
  VikingBotInteractionDirectoryPermission,
  VikingBotInteractionTargetRequest,
} from '@openviking/react/components/viking-bot/interaction';
import type { FileTreeRow } from '@openviking/react/components/file-tree';

import { INITIAL, pause } from '#viking-bot-directory-data';

/** Fixed data and simulated operations only; the component owns every interaction. */
export function useVikingBotDirectory(
  isZh: boolean,
  readOnly: boolean,
  failCreate: boolean,
): VikingBotInteractionTargetRequest['directoryPicker'] & {
  validate: (uri: string) => Promise<string>;
} {
  const [permissionUri, setPermissionUri] = useState(
    'viking://resources/wiki/archive',
  );
  const [permission, setPermission] =
    useState<VikingBotInteractionDirectoryPermission>({
      uri: permissionUri,
      checking: true,
    });
  const [catalog, setCatalog] = useState(INITIAL);
  const [expanded, setExpanded] = useState(new Set<string>());
  const [pages, setPages] = useState<Record<string, number>>({});
  const [query, setQuery] = useState('');
  const [visibleQuery, setVisibleQuery] = useState('');
  const [isReady, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);
  // Simulates an external directory service for the current search; cancel its timer on query changes.
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    void pause(abort.signal).then(
      () => {
        setReady(true);
        setLoading(false);
        if (query === 'error') setFailed(true);
        else {
          setVisibleQuery(query);
          setFailed(false);
        }
      },
      () => {},
    );
    return () => abort.abort();
  }, [query, reload]);
  const rows = useMemo(() => {
    const result: FileTreeRow[] = [];
    const add = (item: (typeof INITIAL)[number], depth: number) =>
      result.push({
        kind: 'entry',
        type: 'directory',
        id: item.uri,
        uri: item.uri,
        name: item.name,
        depth,
        hasChildren:
          !visibleQuery && catalog.some((child) => child.parent === item.uri),
        expanded: expanded.has(item.uri),
        access: item.name === 'read-only' ? 'read-only' : undefined,
      });
    const walk = (parent: string, depth: number) => {
      const children = catalog.filter((item) => item.parent === parent);
      const count = parent ? (pages[parent] ?? 1) : children.length;
      for (const item of children.slice(0, count)) {
        add(item, depth);
        if (expanded.has(item.uri)) walk(item.uri, depth + 1);
      }
      if (children.length > count)
        result.push({
          kind: 'load-more',
          id: `${parent}:more:${count}`,
          parentId: parent,
          depth,
        });
    };
    if (visibleQuery)
      catalog
        .filter((item) =>
          item.name.toLowerCase().includes(visibleQuery.toLowerCase()),
        )
        .forEach((item) => add(item, 0));
    else walk('', 0);
    return result;
  }, [catalog, expanded, pages, visibleQuery]);
  const readDirectory = useCallback(
    async (uri: string, { signal }: { signal: AbortSignal }) => {
      await pause(signal);
      return {
        uri,
        isDirectory: catalog.some((item) => item.uri === uri),
        canWrite: !readOnly && !uri.includes('/read-only'),
      };
    },
    [catalog, readOnly],
  );
  // Simulates metadata from the host service; selection and permission changes cancel stale reads.
  useEffect(() => {
    const abort = new AbortController();
    setPermission({ uri: permissionUri, checking: true });
    void readDirectory(permissionUri, { signal: abort.signal }).then(
      (info) => {
        if (!abort.signal.aborted) setPermission({ uri: permissionUri, info });
      },
      (error) => {
        if (!abort.signal.aborted) setPermission({ uri: permissionUri, error });
      },
    );
    return () => abort.abort();
  }, [permissionUri, readDirectory]);
  const createDirectory = useCallback(
    async (
      parent: string,
      name: string,
      { signal }: { signal: AbortSignal },
    ) => {
      await pause(signal);
      if (failCreate)
        throw new Error(
          isZh
            ? '模拟创建失败，请修改后重试'
            : 'Simulated creation failure. Edit and retry.',
        );
      if (readOnly || parent.includes('/read-only'))
        throw new Error(
          isZh ? '目录权限已撤销' : 'Write permission was revoked.',
        );
      const uri = `${parent}/${name}`;
      if (catalog.some((item) => item.uri === uri))
        throw new Error(isZh ? '目录已存在' : 'The directory already exists.');
      setCatalog((value) => [...value, { uri, name, parent }]);
      setExpanded((value) => new Set([...value, parent]));
      return uri;
    },
    [catalog, failCreate, isZh, readOnly],
  );
  return {
    validate: async (uri: string) => {
      const info = await readDirectory(uri, {
        signal: new AbortController().signal,
      });
      if (!info.isDirectory || !info.canWrite)
        throw new Error(
          isZh
            ? '目录不可写，请重新选择。'
            : 'This directory is read-only. Choose another directory.',
        );
      return info.uri;
    },
    rows,
    permission,
    onSelectionChange: setPermissionUri,
    query,
    onQueryChange: setQuery,
    isReady,
    loading,
    error: failed
      ? isZh
        ? '模拟目录加载失败，当前保留上次结果'
        : 'Simulated loading failure; previous results are retained.'
      : undefined,
    operations: {
      createDirectory,
      toggleExpand: async (row) => {
        await pause();
        setExpanded((value) => {
          const next = new Set(value);
          if (next.has(row.uri)) next.delete(row.uri);
          else next.add(row.uri);
          return next;
        });
      },
      loadMore: async (row) => {
        await pause();
        setPages((value) => ({
          ...value,
          [row.parentId ?? '']: (value[row.parentId ?? ''] ?? 1) + 1,
        }));
      },
      refresh: () => {
        setQuery('');
        setReload((value) => value + 1);
        return Promise.resolve();
      },
    },
  };
}
