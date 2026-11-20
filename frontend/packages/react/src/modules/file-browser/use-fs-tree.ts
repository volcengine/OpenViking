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
  const expansionRun = useRef(0);
  const toggleExpand = useCallback<FsTreeState['toggleExpand']>(
    async (row) => {
      if (row.disabled) return;
      cancelReveal();
      const run = ++expansionRun.current;
      const scope = inputs.scope.current();
      const opening = !inputs.isExpanded(row.uri);
      await toggleTree(row);
      if (!opening) return;
      // Release the clicked row's pending operation while descendants load,
      // so the user can fold the parent and invalidate this chain.
      const expandChildren = async () => {
        let parent = row.uri;
        const visited = new Set([parent]);
        while (
          run === expansionRun.current &&
          inputs.scope.current() === scope &&
          !scope.controller.signal.aborted
        ) {
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
          await inputs.fetchPage(child.uri, 0);
          parent = child.uri;
        }
      };
      void expandChildren();
    },
    [cancelReveal, inputs, toggleTree],
  );
  return {
    ...tree,
    ...useTreeMetadata(rootUri, dirs, identifyUri),
    ...navigation,
    toggleExpand,
  };
}
