import { useEffect, useRef, type RefObject } from 'react';
import type { FileTreeHandle } from '../file-tree/types.js';
import type { ContextPickerProps } from './types.js';

export const normalizeUri = (uri: string) => uri.replace(/\/+$/, '');

/** 只发出树导航意图；目录数据、权限与请求均由宿主维护。 */
export function useContextReveal(
  props: ContextPickerProps,
  tree: RefObject<FileTreeHandle | null>,
) {
  const initial = useRef(props.value.map((entry) => normalizeUri(entry.uri)));
  const stopped = useRef(false);
  const done = useRef(false);
  const attempts = useRef(new Set<string>());
  // 与提交后的树 DOM 同步：缺失祖先时请求宿主展开/翻页，节点出现后滚动。
  // 清理滚动计时器；不发请求、不保存服务端数据；手动导航会停止自动定位。
  useEffect(() => {
    if (
      props.autoReveal === false ||
      stopped.current ||
      done.current ||
      !initial.current.length ||
      props.query?.trim() ||
      props.loading ||
      props.isReady === false ||
      props.disabled ||
      props.confirming
    )
      return;
    const entries = props.rows.filter((row) => row.kind === 'entry');
    const contains = (uri: string) =>
      initial.current.some(
        (selected) =>
          selected === normalizeUri(uri) ||
          selected.startsWith(`${normalizeUri(uri)}/`),
      );
    const ancestor = entries.find(
      (row) =>
        initial.current.some((uri) =>
          uri.startsWith(`${normalizeUri(row.uri)}/`),
        ) &&
        !row.expanded &&
        row.hasChildren !== false &&
        !row.disabled &&
        !row.busy,
    );
    const missing = props.rows.find(
      (row) =>
        row.kind === 'load-more' &&
        (!row.parentId ||
          entries.some(
            (entry) => entry.id === row.parentId && contains(entry.uri),
          )),
    );
    const selected = entries.filter((row) =>
      initial.current.includes(normalizeUri(row.uri)),
    );
    const action =
      ancestor && props.operations?.toggleExpand
        ? () => props.operations!.toggleExpand!(ancestor)
        : selected.length < initial.current.length &&
            missing?.kind === 'load-more' &&
            props.operations?.loadMore
          ? () => props.operations!.loadMore!(missing)
          : undefined;
    if (action) {
      const key = JSON.stringify([
        ancestor?.id ?? missing?.id,
        props.rows.map((row) => [row.id, row.kind === 'entry' && row.expanded]),
      ]);
      if (!attempts.current.has(key)) {
        attempts.current.add(key);
        // The host presents loading/errors and owns retry. Rejection stops this reveal attempt.
        void action().catch(() => {});
      }
      return;
    }
    const first = selected[0];
    if (!first) return;
    const timer = setTimeout(() => {
      if (
        !stopped.current &&
        tree.current?.scrollToRow(first.id, { block: 'nearest' })
      )
        done.current = true;
    }, 300);
    return () => clearTimeout(timer);
  }, [props, tree]);
  return () => {
    stopped.current = true;
  };
}
