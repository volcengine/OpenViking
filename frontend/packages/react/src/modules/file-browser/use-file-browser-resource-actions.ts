import { useCallback, useMemo } from 'react';
import type {
  FileBrowserActions,
  UseFileBrowserOptions,
} from './file-browser-state-types.js';
import type { useBrowserDashboards } from './use-browser-dashboards.js';
import type { useFileBrowserTreeActions } from './use-file-browser-tree-actions.js';
import type { useFsTree } from './use-fs-tree.js';
import { dashboardKey } from './dashboard-model.js';
import { useStableCallback } from './use-stable-callback.js';

interface Options extends Pick<UseFileBrowserOptions, 'onSelect'> {
  confirmLeave: NonNullable<UseFileBrowserOptions['confirmLeave']>;
  activeUri: string | null;
  boards: Pick<
    ReturnType<typeof useBrowserDashboards>,
    'open' | 'close' | 'isOpen'
  >;
  tree: Pick<
    ReturnType<typeof useFsTree>,
    'open' | 'toggleExpand' | 'cancelReveal' | 'reveal'
  >;
  treeActions: ReturnType<typeof useFileBrowserTreeActions>['actions'];
  requestUri: (resource: string) => string;
  openResource: FileBrowserActions['openUri'];
  selectUri: (uri: string) => void;
  setActiveKey: (key: string) => void;
}

export function useFileBrowserResourceActions({
  activeUri,
  boards: { open: openDashboard, close: closeDashboard, isOpen },
  tree: { open: openTree, cancelReveal, reveal, toggleExpand },
  treeActions,
  requestUri,
  openResource,
  confirmLeave,
  setActiveKey,
  selectUri,
  onSelect,
}: Options) {
  const chooseUri = useStableCallback(selectUri);
  const notifySelect = useStableCallback(
    (row: Parameters<FileBrowserActions['select']>[0]) => onSelect?.(row),
  );
  const navigateResource = useCallback<FileBrowserActions['openResource']>(
    async (resource, options) => {
      const uri = requestUri(resource);
      cancelReveal();
      closeDashboard();
      chooseUri(uri);
      return options?.reveal === false ? true : reveal(uri);
    },
    [cancelReveal, chooseUri, closeDashboard, requestUri, reveal],
  );
  return useMemo<FileBrowserActions>(
    () => ({
      select: async (row) => {
        if (row.disabled || row.selectable === false) return;
        const leave = row.uri === activeUri || confirmLeave();
        if (leave !== true && !(await leave)) return;
        cancelReveal();
        closeDashboard();
        // Report the user's selection before an asynchronous tree expansion.
        chooseUri(row.uri);
        notifySelect(row);
        if (row.type === 'directory') void openTree(row.uri);
      },
      toggleExpand,
      ...treeActions,
      openUri: openResource,
      openResource: navigateResource,
      reveal,
      cancelReveal,
      openDashboard: (uri) => {
        const leave = (isOpen && uri === activeUri) || confirmLeave();
        if (leave === true) openDashboard(uri);
        else if (leave)
          void leave.then((confirmed) => {
            if (confirmed) openDashboard(uri);
          });
      },
      setPreviewKey: (key) => {
        if (activeUri && key === dashboardKey(activeUri))
          openDashboard(activeUri);
        else {
          closeDashboard();
          setActiveKey(key);
        }
      },
    }),
    [
      activeUri,
      cancelReveal,
      chooseUri,
      closeDashboard,
      confirmLeave,
      isOpen,
      navigateResource,
      notifySelect,
      openDashboard,
      openResource,
      openTree,
      reveal,
      setActiveKey,
      toggleExpand,
      treeActions,
    ],
  );
}
