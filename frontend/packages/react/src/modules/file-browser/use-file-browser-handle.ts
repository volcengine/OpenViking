import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import type { ForwardedRef } from 'react';
import type { FileTreeHandle } from '../../components/file-tree/index.js';
import type { FileBrowserApi, FileBrowserHandle } from './types.js';
import { toError } from './fs-model.js';
import { parseSummaryUri } from './fs-summary-uri.js';

interface ScrollRequest {
  uri: string;
  resolve: (revealed: boolean) => void;
}

export function useFileBrowserHandle(
  browser: FileBrowserApi,
  ref: ForwardedRef<FileBrowserHandle>,
) {
  const treeRef = useRef<FileTreeHandle>(null);
  const active = useRef<ScrollRequest | null>(null);
  const [scroll, setScroll] = useState<ScrollRequest | null>(null);
  const open = browser.actions.openResource;
  const { rows, currentUri, selectedId, identifyUri } = browser;
  const openResource = useCallback<FileBrowserHandle['openResource']>(
    (uri, options) => {
      active.current?.resolve(false);
      return new Promise<boolean>((resolve, reject) => {
        // 摘要文件落到所属目录，滚动与选中校验都以那一行为准。
        // A summary file lands on its directory; the scroll and selection check
        // both use that row.
        const request = {
          uri: parseSummaryUri(uri)?.directory ?? uri,
          resolve,
        };
        active.current = request;
        void open(uri, options).then(
          (found) => {
            if (active.current !== request) return;
            if (!found || options?.reveal === false) {
              active.current = null;
              resolve(found);
            } else setScroll(request);
          },
          (cause: unknown) => {
            if (active.current === request) {
              active.current = null;
              reject(toError(cause));
            }
          },
        );
      });
    },
    [open],
  );
  const refresh = browser.actions.refresh;
  useImperativeHandle(ref, () => ({ openResource, refresh }), [
    openResource,
    refresh,
  ]);
  // Synchronize the requested scroll with committed tree DOM. React may commit
  // rows after the network finishes; event-time scrolling cannot find them yet.
  // Requests are consumed once, so ordinary URI changes never scroll the tree.
  useEffect(() => {
    if (!scroll || active.current !== scroll) return;
    const target = identifyUri(scroll.uri);
    const row = rows.find(
      (candidate) =>
        candidate.kind === 'entry' && identifyUri(candidate.uri) === target,
    );
    const selected = currentUri ? identifyUri(currentUri) : null;
    active.current = null;
    scroll.resolve(
      selected === target &&
        row?.id === selectedId &&
        !!row &&
        !!treeRef.current?.scrollToRow(row.id),
    );
  }, [scroll, rows, currentUri, selectedId, identifyUri]);
  // The public command is tied to a mounted network scope. On scope change or
  // unmount, settle its pending result; the tree loader aborts network requests.
  useEffect(
    () => () => {
      active.current?.resolve(false);
      active.current = null;
    },
    [open],
  );
  return { treeRef, openResource };
}
