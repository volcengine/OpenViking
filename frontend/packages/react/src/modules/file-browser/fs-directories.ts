import { useCallback, useMemo } from 'react';

import { toEntryRow } from './fs-model.js';
import type { DirState } from './fs-model.js';
/**
 * 树已知的目录 URI 集合：根目录，加上每一层已加载条目里标记为目录的那些。
 *
 * 它与「当前可见的行」无关——根目录从不出现在展平列表里，祖先折叠后其后代也会
 * 消失，但它们的类型不会因此改变。预览据此决定读 L0/L1 摘要还是读正文。
 *
 * The set of directory URIs the tree knows about: the root plus every loaded entry
 * marked as a directory.
 *
 * It is independent of the rows currently visible — the root never appears in the
 * flattened list, and a descendant disappears when its ancestor collapses, yet
 * neither changes type. The preview decides between an L0/L1 summary and content
 * from this set.
 */
export function knownDirectories(
  rootUri: string,
  dirs: Record<string, DirState>,
  identifyUri: (uri: string) => string,
): ReadonlySet<string> {
  const known = new Set<string>([identifyUri(rootUri)]);
  for (const [parent, level] of Object.entries(dirs)) {
    known.add(identifyUri(parent));
    for (const entry of level.entries) {
      if (entry.isDir === true) known.add(identifyUri(entry.uri));
    }
  }
  return known;
}

/**
 * 树里出现过的文件 URI。与 {@link knownDirectories} 一起构成「类型已知」的判定：
 * 两者都不包含，说明这个 URI 还没被加载过，类型未知——此时不能假定它是文件。
 *
 * The file URIs the tree has seen. Together with {@link knownDirectories} this decides
 * "type known": in neither means the URI was never loaded, so its type is unknown —
 * and it must not be assumed to be a file.
 */
export function knownFiles(
  dirs: Record<string, DirState>,
  identifyUri: (uri: string) => string,
): ReadonlySet<string> {
  const known = new Set<string>();
  for (const level of Object.values(dirs)) {
    for (const entry of level.entries) {
      if (entry.isDir !== true) known.add(identifyUri(entry.uri));
    }
  }
  return known;
}

/**
 * 「这个 URI 是树里见过的文件」的判定函数。引用跟着文件集合走，因此同一份加载
 * 结果下它是稳定的，可以作为依赖。
 *
 * The predicate "this URI is a file the tree has seen". Its identity follows the file
 * set, so it stays stable for one load result and can be used as a dependency.
 */
export function useKnownFile(
  dirs: Record<string, DirState>,
  identifyUri: (uri: string) => string,
): (uri: string) => boolean {
  const files = useMemo(
    () => knownFiles(dirs, identifyUri),
    [dirs, identifyUri],
  );
  return useCallback(
    (uri: string) => files.has(identifyUri(uri)),
    [files, identifyUri],
  );
}

/** Loaded resource metadata survives collapsed ancestors, unlike the visible rows. */
export function useTreeMetadata(
  rootUri: string,
  dirs: Record<string, DirState>,
  identifyUri: (uri: string) => string,
) {
  const directoryUris = useMemo(
    () => knownDirectories(rootUri, dirs, identifyUri),
    [rootUri, dirs, identifyUri],
  );
  const isKnownFile = useKnownFile(dirs, identifyUri);
  const metadataRows = useMemo(
    () =>
      Object.values(dirs).flatMap((dir) =>
        dir.entries.map((entry) => toEntryRow(entry, 0, false)),
      ),
    [dirs],
  );
  const accessOf = useCallback(
    (uri: string | null) => {
      if (!uri) return;
      const target = identifyUri(uri);
      const row = metadataRows.find(
        (entry) => identifyUri(entry.uri) === target,
      );
      return row?.access === 'denied' || row?.access === 'container'
        ? row.access
        : undefined;
    },
    [metadataRows, identifyUri],
  );
  const resourceTypeOf = (uri: string | null) => {
    if (uri === null) return 'file';
    if (directoryUris.has(identifyUri(uri))) return 'directory';
    return isKnownFile(uri) ? 'file' : 'unknown';
  };
  return { directoryUris, metadataRows, accessOf, identifyUri, resourceTypeOf };
}
