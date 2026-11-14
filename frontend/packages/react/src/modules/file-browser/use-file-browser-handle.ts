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
import { resolveResourceNavigation } from './resource-navigation.js';

interface ScrollRequest {
  uri: string;
  resolve: (revealed: boolean) => void;
}

export function useFileBrowserHandle(
  browser: FileBrowserApi,
  ref: ForwardedRef<FileBrowserHandle>,
  rootUri: string,
) {
  const treeRef = useRef<FileTreeHandle>(null);
  const treeScrollRef = useRef<HTMLDivElement>(null);
  const active = useRef<ScrollRequest | null>(null);
  const [scroll, setScroll] = useState<ScrollRequest | null>(null);
  const open = browser.actions.openResource;
  const { rows, currentUri, selectedId, identifyUri } = browser;
  const openResource = useCallback<FileBrowserHandle['openResource']>(
    (uri, options) => {
      active.current?.resolve(false);
      return new Promise<boolean>((resolve, reject) => {
        const request = { uri: resolveResourceNavigation(uri).uri, resolve };
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
  useImperativeHandle(ref, () => ({ openResource }), [openResource]);
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
    if (selected === target && target === identifyUri(rootUri)) {
      // FileTree scrolls inside its tree body; the surrounding pane only clips.
      const tree =
        treeScrollRef.current?.querySelector<HTMLElement>('[role="tree"]');
      if (tree) tree.scrollTop = 0;
      scroll.resolve(!!tree);
      return;
    }
    scroll.resolve(
      selected === target &&
        row?.id === selectedId &&
        !!row &&
        !!treeRef.current?.scrollToRow(row.id),
    );
  }, [scroll, rows, currentUri, selectedId, identifyUri, rootUri]);
  // The public command is tied to a mounted network scope. On scope change or
  // unmount, settle its pending result; the tree loader aborts network requests.
  useEffect(
    () => () => {
      active.current?.resolve(false);
      active.current = null;
    },
    [open],
  );
  return { treeRef, treeScrollRef, openResource };
}
