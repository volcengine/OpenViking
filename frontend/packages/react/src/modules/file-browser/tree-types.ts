import type { Workflow } from '../../shared/monitoring/workflow.js';
import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { useTreeMetadata } from './fs-directories.js';

export interface FsTreeState extends ReturnType<typeof useTreeMetadata> {
  monitoring: readonly Workflow<'file_browser_directory_load'>[];
  rows: readonly FileTreeRow[];
  isReady: boolean;
  error: Error | null;
  toggleExpand: (row: FileTreeEntryRow) => Promise<void>;
  open: (uri: string) => Promise<void>;
  openPath: (uri: string) => Promise<void>;
  reveal: (uri: string) => Promise<boolean>;
  cancelReveal: () => void;
  loadMore: (parentId: string) => Promise<void>;
  retry: (parentId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

export interface UseFsTreeOptions {
  client: OvClient | null;
  rootUri: string;
  /**
   * 首次加载后自动展开到的路径，逐级展开并加载子节点。省略时只展开根。
   * Path to expand to after the first load, opening and loading each level.
   * Omitting it leaves only the root expanded.
   */
  defaultExpandedUri?: string;
  requestOptions?: OvRequestOptions;
}
