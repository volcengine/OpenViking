import { useEffect, useMemo, useState } from 'react';
import type { FileTreeRow } from '@openviking/react/components/file-tree';
import { contextPickerCatalog } from '#context-picker-data';

/** 离线数据适配；不实现弹窗、选择、键盘或自动定位。 */
export function useContextPickerDemo(
  mode: 'sources' | 'target' | 'skill',
  isZh: boolean,
) {
  const [catalog, setCatalog] = useState(contextPickerCatalog);
  const [query, setQuery] = useState('');
  const [loadedQuery, setLoadedQuery] = useState('');
  const [expanded, setExpanded] = useState(new Set<string>());
  const [pages, setPages] = useState<Record<string, number>>({});
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  // 模拟外部搜索服务：查询与重试改变时重新读取，清理定时器以忽略过期结果。
  useEffect(() => {
    setLoading(true);
    const timer = setTimeout(() => {
      setReady(true);
      setLoading(false);
      if (query === 'error') setFailed(true);
      else {
        setLoadedQuery(query);
        setFailed(false);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [query, revision]);
  const rows = useMemo(() => {
    const result: FileTreeRow[] = [];
    const visible = catalog.filter((item) =>
      mode !== 'skill'
        ? !item.name.endsWith('.md')
        : item.uri.startsWith('viking://system'),
    );
    const add = (item: (typeof catalog)[number], depth: number) => {
      const file = item.name.endsWith('.md');
      result.push({
        kind: 'entry',
        id: item.uri,
        uri: item.uri,
        name: item.name,
        depth,
        type: file ? 'file' : 'directory',
        hasChildren:
          !file &&
          !loadedQuery &&
          visible.some((child) => child.parent === item.uri),
        expanded: expanded.has(item.uri),
        selectable:
          mode === 'skill'
            ? file
            : mode === 'target'
              ? item.name !== 'read-only'
              : true,
        access: item.name === 'read-only' ? 'read-only' : undefined,
      });
    };
    const walk = (parent: string, depth: number) => {
      const children = visible.filter((item) => item.parent === parent);
      const count = parent ? (pages[parent] ?? 1) : children.length;
      for (const child of children.slice(0, count)) {
        add(child, depth);
        if (expanded.has(child.uri)) walk(child.uri, depth + 1);
      }
      if (children.length > count)
        result.push({
          kind: 'load-more',
          id: `${parent}:${count}`,
          parentId: parent,
          depth,
        });
    };
    if (loadedQuery)
      visible
        .filter((item) =>
          item.name.toLowerCase().includes(loadedQuery.toLowerCase()),
        )
        .forEach((item) => add(item, 0));
    else walk('', 0);
    return result;
  }, [catalog, mode, loadedQuery, expanded, pages]);
  const add = (parent: string, name: string) => {
    const uri = `${parent}/${name}`;
    setCatalog((items) =>
      items.some((item) => item.uri === uri)
        ? items
        : [...items, { uri, name, parent }],
    );
    setExpanded((items) => new Set([...items, parent]));
    setPages((items) => ({
      ...items,
      [parent]: catalog.filter((item) => item.parent === parent).length + 1,
    }));
    setQuery('');
    return uri;
  };
  return {
    rows,
    query,
    onQueryChange: setQuery,
    isReady: ready,
    loading,
    error: failed
      ? isZh
        ? '模拟搜索失败，保留上次结果。'
        : 'Simulated search failure. Previous results retained.'
      : undefined,
    add,
    retry: () => setRevision((value) => value + 1),
    operations: {
      toggleExpand: (row: { uri: string }) => {
        setExpanded((items) => {
          const next = new Set(items);
          if (next.has(row.uri)) next.delete(row.uri);
          else next.add(row.uri);
          return next;
        });
        return Promise.resolve();
      },
      loadMore: (row: { parentId?: string | null }) => {
        setPages((items) => ({
          ...items,
          [row.parentId ?? '']: (items[row.parentId ?? ''] ?? 1) + 1,
        }));
        return Promise.resolve();
      },
    },
  };
}
