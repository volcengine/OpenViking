import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import type { ForwardedRef, RefObject } from 'react';
import type { FileTreeHandle } from '../../components/file-tree/index.js';
import type { FileBrowserApi } from './file-browser-state-types.js';
import type { FileBrowserHandle } from './types.js';
import { toError } from '../../shared/request/fs-errors.js';

import { resolveResourceNavigation } from './resource-navigation.js';

interface ScrollRequest {
  uri: string;
  resolve: (revealed: boolean) => void;
}

export function useFileBrowserHandle(
  browser: FileBrowserApi,
  ref: ForwardedRef<FileBrowserHandle>,
  rootUri: string,
  controlledUri: string | undefined,
  reportedUri: RefObject<string | undefined>,
  choosePackFile: FileBrowserHandle['choosePackFile'],
  restoreSelection: boolean,
) {
  const treeRef = useRef<FileTreeHandle>(null);
  const treeScrollRef = useRef<HTMLDivElement>(null);
  const active = useRef<ScrollRequest | null>(null);
  const [scroll, setScroll] = useState<ScrollRequest | null>(null);
  const { openResource: open, reveal, cancelReveal } = browser.actions;
  const { rows, currentUri, selectedId, identifyUri, isReady } = browser;
  const previousUri = useRef(controlledUri);
  const pendingUri = useRef(restoreSelection ? controlledUri : undefined);
  const preserveInitialExpansion = useRef(restoreSelection);
  const cancelScroll = useCallback(() => {
    active.current?.resolve(false);
    active.current = null;
  }, []);
  const navigate = useCallback(
    (uri: string, run: () => Promise<boolean>, shouldScroll = true) => {
      cancelScroll();
      return new Promise<boolean>((resolve, reject) => {
        const request = { uri, resolve };
        active.current = request;
        void run().then(
          (found) => {
            if (active.current !== request) return;
            if (!found || !shouldScroll) {
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
    [cancelScroll],
  );
  const openResource = useCallback<FileBrowserHandle['openResource']>(
    (uri, options) =>
      navigate(
        resolveResourceNavigation(uri).uri,
        () => open(uri, options),
        options?.reveal !== false,
      ),
    [navigate, open],
  );
  useImperativeHandle(ref, () => ({ openResource, choosePackFile }), [
    openResource,
    choosePackFile,
  ]);
  // Sync the tree with a selection committed by an external owner. The host
  // can change the prop without a FileBrowser event. A newer selection or
  // scope supersedes the outstanding navigation and scroll.
  useEffect(() => {
    if (previousUri.current !== controlledUri) {
      previousUri.current = controlledUri;
      preserveInitialExpansion.current = false;
      const fromBrowser =
        controlledUri !== undefined && reportedUri.current === controlledUri;
      reportedUri.current = undefined;
      pendingUri.current = undefined;
      if (fromBrowser) return;
      if (!controlledUri || !isReady) {
        cancelReveal();
        cancelScroll();
      }
      pendingUri.current = controlledUri || undefined;
    }
    const target = pendingUri.current;
    if (!target || !isReady) return;
    pendingUri.current = undefined;
    // The preview already follows the prop. Listing failures are rendered in the tree.
    const preserveExpansion = preserveInitialExpansion.current;
    preserveInitialExpansion.current = false;
    void navigate(target, () => reveal(target, preserveExpansion)).catch(
      () => {},
    );
  }, [
    controlledUri,
    isReady,
    reportedUri,
    navigate,
    reveal,
    cancelReveal,
    cancelScroll,
  ]);
  // Synchronize the requested scroll with committed tree DOM. React may commit
  // rows after the network finishes; event-time scrolling cannot find them yet.
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
  useEffect(() => cancelScroll, [open, cancelScroll]);
  return { treeRef, treeScrollRef, openResource };
}
