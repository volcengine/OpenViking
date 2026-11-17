import { useDirectoryTree } from '../../shared/directory-tree/use-directory-tree.js';
import type { UseDirectoryTreeOptions } from '../../shared/directory-tree/types.js';
import { useTreeMetadata } from './fs-directories.js';
import { useFsTreeReveal } from './use-fs-tree-reveal.js';
import { useResourceUriIdentity } from './use-resource-uri-identity.js';
import type { FsTreeState } from './tree-types.js';

export function useFsTree(options: UseDirectoryTreeOptions): FsTreeState {
  const { rootUri, requestOptions } = options;
  const { navigationInputs, dirs, ...tree } = useDirectoryTree(options);
  const { request, ...inputs } = navigationInputs;
  const identity = useResourceUriIdentity(
    request?.client ?? null,
    request?.options,
    rootUri,
  );
  const navigation = useFsTreeReveal({
    ...inputs,
    rootUri,
    resolveHome: identity.resolveHome,
    requestSignal: requestOptions?.signal,
  });
  return {
    ...tree,
    ...useTreeMetadata(rootUri, dirs, identity.identifyUri),
    ...navigation,
  };
}
