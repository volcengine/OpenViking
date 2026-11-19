import { useDirectoryTree } from '../../shared/directory-tree/use-directory-tree.js';
import type { UseDirectoryTreeOptions } from '../../shared/directory-tree/types.js';
import { useTreeMetadata } from './fs-directories.js';
import { useFsTreeReveal } from './use-fs-tree-reveal.js';
import type { FsTreeState } from './tree-types.js';

export function useFsTree(options: UseDirectoryTreeOptions): FsTreeState {
  const { rootUri, requestOptions } = options;
  const { navigationInputs, dirs, ...tree } = useDirectoryTree(options);
  const { identifyUri, ...inputs } = navigationInputs;
  const navigation = useFsTreeReveal({
    ...inputs,
    rootUri,
    requestSignal: requestOptions?.signal,
  });
  return {
    ...tree,
    ...useTreeMetadata(rootUri, dirs, identifyUri),
    ...navigation,
  };
}
