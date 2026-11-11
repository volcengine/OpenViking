import { useRef, useState } from 'react';

import type {
  FileTreeEntryRow,
  FileTreeErrorRow,
  FileTreeLoadMoreRow,
  FileTreeProps,
} from './types.js';

type ActionProps = Pick<
  FileTreeProps,
  | 'operations'
  | 'onSelect'
  | 'onToggleExpand'
  | 'onLoadMore'
  | 'onRetry'
  | 'onOpenRetentionSettings'
  | 'onSearch'
  | 'onRefresh'
>;

/**
 * 把 `operations` 包成带成功通知的回调：同一操作未完成时忽略重复触发，
 * 失败时不触发通知。
 *
 * Wraps `operations` with success notifications: a repeated request for the same
 * pending operation is ignored, and a rejection fires no notification.
 */
export function useFileTreeActions(props: ActionProps) {
  const pending = useRef(new Set<string>());
  const [refreshing, setRefreshing] = useState(false);

  const {
    select,
    toggleExpand,
    loadMore,
    retry,
    openRetentionSettings,
    search,
    refresh,
  } = props.operations ?? {};

  async function run<T>(
    key: string,
    execute: (value: T) => Promise<void>,
    notify: ((value: T) => void) | undefined,
    value: T,
  ) {
    if (pending.current.has(key)) return;
    pending.current.add(key);
    try {
      await execute(value);
    } catch {
      return;
    } finally {
      pending.current.delete(key);
    }
    notify?.(value);
  }

  return {
    refreshing,
    onSearch: search
      ? () => void run('search', search, props.onSearch, undefined)
      : undefined,
    onRefresh: refresh
      ? () => {
          if (pending.current.has('refresh')) return;
          setRefreshing(true);
          void run('refresh', refresh, props.onRefresh, undefined).finally(() =>
            setRefreshing(false),
          );
        }
      : undefined,
    onSelect: select
      ? (row: FileTreeEntryRow) =>
          void run(`select:${row.id}`, select, props.onSelect, row)
      : undefined,
    onToggleExpand: toggleExpand
      ? (row: FileTreeEntryRow) =>
          void run(`toggle:${row.id}`, toggleExpand, props.onToggleExpand, row)
      : undefined,
    onLoadMore: loadMore
      ? (row: FileTreeLoadMoreRow) =>
          void run(`load:${row.id}`, loadMore, props.onLoadMore, row)
      : undefined,
    onRetry: retry
      ? (row: FileTreeErrorRow) =>
          void run(`retry:${row.id}`, retry, props.onRetry, row)
      : undefined,
    onOpenRetentionSettings: openRetentionSettings
      ? (uri: string) =>
          void run(
            `retention:${uri}`,
            openRetentionSettings,
            props.onOpenRetentionSettings,
            uri,
          )
      : undefined,
  };
}
