import { useRef } from 'react';

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
>;

export function useFileTreeActions(props: ActionProps) {
  const pending = useRef(new Set<string>());
  const { select, toggleExpand, loadMore, retry, openRetentionSettings } =
    props.operations ?? {};

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
