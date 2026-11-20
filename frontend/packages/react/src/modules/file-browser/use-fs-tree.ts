import { useCallback } from 'react';
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
  const toggleExpand = useCallback<FsTreeState['toggleExpand']>(
    (row) => {
      if (!row.disabled) cancelReveal();
      return toggleTree(row);
    },
    [cancelReveal, toggleTree],
  );
  return {
    ...tree,
    ...useTreeMetadata(rootUri, dirs, identifyUri),
    ...navigation,
    toggleExpand,
  };
}
