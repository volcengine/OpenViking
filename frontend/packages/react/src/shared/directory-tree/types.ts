import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { Workflow } from '../monitoring/workflow.js';

/**
 * 目录接口返回的一行。`ls` 的 `output=agent` 不返回 `name`，名称从 URI 末段取。
 * One row from the directory API. `ls` with `output=agent` returns no `name`, so
 * the name comes from the last URI segment.
 */
export interface FsEntry {
  uri: string;
  isDir?: boolean;
  size?: number;
  modTime?: string;
  abstract?: string;
  tags?: readonly string[];
  access?: 'denied' | 'container' | 'read' | 'write' | 'manage';
}

/** 一个目录已加载的子节点与分页状态。 */
export interface DirState {
  monitoring?: readonly Workflow<'file_browser_directory_load'>[];
  ready: boolean;
  entries: FsEntry[];
  hasMore: boolean;
  loading: boolean;
  error: Error | null;
}

export interface DirectoryExpansionEdit {
  uri: string;
  expanded: boolean;
}

export interface DirectoryExpansionSnapshot {
  expandedUris: readonly string[];
  collapsedUris: readonly string[];
  pendingEdits?: readonly DirectoryExpansionEdit[];
}

export interface UseDirectoryTreeOptions {
  client: OvClient | null;
  rootUri: string;
  /** Presentation only; cached entries and paging retain the server response. */
  hideDeniedEntries?: boolean;
  /** Isolates cached rows when the host changes an opaque identity. */
  cacheScopeKey?: string;
  /**
   * 首次加载后自动展开到的路径，逐级展开并加载子节点。省略时只展开根。
   * Path to expand to after the first load, opening and loading each level.
   * Omitting it leaves only the root expanded.
   */
  defaultExpandedUri?: string;
  defaultExpandedUris?: readonly string[];
  defaultExpansionSnapshot?: DirectoryExpansionSnapshot;
  onExpandedUrisChange?: (uris: readonly string[]) => void;
  onExpansionSnapshotChange?: (snapshot: DirectoryExpansionSnapshot) => void;
  requestOptions?: OvRequestOptions;
}
