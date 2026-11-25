import { useCallback, useRef } from 'react';
import { useDirectoryTree } from '../../shared/directory-tree/use-directory-tree.js';
import type { UseDirectoryTreeOptions } from '../../shared/directory-tree/types.js';
import { useTreeMetadata } from './fs-directories.js';
import { useFsTreeReveal } from './use-fs-tree-reveal.js';
import type { FsTreeState } from './tree-types.js';

export function useFsTree(options: UseDirectoryTreeOptions): FsTreeState {
  const { rootUri, requestOptions } = options;
  const {
    navigationInputs,
    dirs,
    toggleExpand: toggleTree,
    ...tree
  } = useDirectoryTree(options);
  const { identifyUri, ...inputs } = navigationInputs;
  const navigation = useFsTreeReveal({
    ...inputs,
    rootUri,
    requestSignal: requestOptions?.signal,
  });
  const { cancelReveal } = navigation;
  const activeChains = useRef(new Set<{ parent: string }>());
  const toggleExpand = useCallback<FsTreeState['toggleExpand']>(
    async (row) => {
      if (row.disabled) return;
      cancelReveal();
      const scope = inputs.scope.current();
      const opening = !inputs.isExpanded(row.uri);
      const firstPage = toggleTree(row);
      if (!opening) {
        const collapsed = identifyUri(row.uri);
        for (const chain of activeChains.current) {
          const parent = identifyUri(chain.parent);
          if (parent === collapsed || parent.startsWith(`${collapsed}/`))
            activeChains.current.delete(chain);
        }
        return firstPage;
      }
      const chain = { parent: row.uri };
      activeChains.current.add(chain);
      const expandChildren = async () => {
        await firstPage;
        const visited = new Set([row.uri]);
        while (
          activeChains.current.has(chain) &&
          inputs.scope.current() === scope &&
          !scope.controller.signal.aborted
        ) {
          const parent = chain.parent;
          const page = inputs.dirs.current[parent];
          if (!page?.ready || page.loading || page.error || page.hasMore)
            return;
          const child = page.entries.length === 1 ? page.entries[0] : undefined;
          if (
            !child?.isDir ||
            child.access === 'denied' ||
            visited.has(child.uri) ||
            !child.uri.startsWith(`${parent.replace(/\/+$/, '')}/`)
          )
            return;
          visited.add(child.uri);
          inputs.expandForNavigation(child.uri);
          chain.parent = child.uri;
          await inputs.fetchPage(child.uri, 0);
        }
      };
      void expandChildren().finally(() => activeChains.current.delete(chain));
      return firstPage;
    },
    [cancelReveal, identifyUri, inputs, toggleTree],
  );
  return {
    ...tree,
    ...useTreeMetadata(rootUri, dirs, identifyUri),
    ...navigation,
    toggleExpand,
  };
}
