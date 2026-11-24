import type { Workflow } from '../../shared/monitoring/workflow.js';
import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import type { useTreeMetadata } from './fs-directories.js';

export interface FsTreeState extends ReturnType<typeof useTreeMetadata> {
  monitoring: readonly Workflow<'file_browser_directory_load'>[];
  rows: readonly FileTreeRow[];
  isReady: boolean;
  isInitializing: boolean;
  error: Error | null;
  toggleExpand: (row: FileTreeEntryRow) => Promise<void>;
  open: (uri: string) => Promise<void>;
  openPath: (uri: string) => Promise<void>;
  reveal: (uri: string, preserveExpansion?: boolean) => Promise<boolean>;
  cancelReveal: () => void;
  loadMore: (parentId: string) => Promise<void>;
  retry: (parentId: string) => Promise<void>;
  refresh: () => Promise<void>;
}
