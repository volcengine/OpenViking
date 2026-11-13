import type { ReactNode } from 'react';
import type {
  FilePreviewActions,
  FilePreviewActionItems,
} from '../../components/file-preview/index.js';

/** Optional visibility flags; true never grants an unsupported capability. */
export interface FileBrowserPreviewActions {
  copyPath?: boolean;
  viewSwitch?: boolean;
  edit?: boolean;
  copyContent?: boolean;
  download?: boolean;
  history?: boolean;
  importPack?: boolean;
  exportPack?: boolean;
  delete?: boolean;
  configure?: boolean;
}

export interface FileBrowserPreviewActionContext extends FilePreviewActions {
  /** Module-generated commands, already filtered by available capabilities. */
  defaultActions: FilePreviewActionItems;
  canDelete: boolean;
  /** Opens the native deletion confirmation; it does not close the preview tab. */
  deleteFile: () => void;
}

export interface FileBrowserPreviewSlots {
  actions?:
    ReactNode | ((context: FileBrowserPreviewActionContext) => ReactNode);
  extraActions?: ReactNode;
  /** Replace the command list; a factory can reuse or reorder defaultActions. */
  actionItems?:
    | FilePreviewActionItems
    | ((context: FileBrowserPreviewActionContext) => FilePreviewActionItems);
}
