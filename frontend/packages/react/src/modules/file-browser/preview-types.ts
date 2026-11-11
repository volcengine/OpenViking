import type { ReactNode } from 'react';
import type {
  FilePreviewActions,
  FilePreviewMenuItem,
} from '../../components/file-preview/index.js';

/** 内置操作开关及宿主流程入口。Built-in controls and host-owned workflow entries. */
export interface FileBrowserPreviewActions {
  copyContent?: boolean;
  edit?: boolean;
  /** 默认启用原生文件删除；readOnly 与管理权限仍生效。Native file deletion is enabled by default. */
  delete?: boolean;
  onHistory?: (uri: string) => void | Promise<void>;
  /** Receives the selected directory, or the selected file's parent. */
  onImportPack?: (parentUri: string) => void | Promise<void>;
  onExportPack?: (uri: string) => void | Promise<void>;
  /** Notification after native deletion succeeds. */
  onDelete?: (uri: string) => void;
}

export interface FileBrowserPreviewActionContext extends FilePreviewActions {
  canDelete: boolean;
  /** Opens the native deletion confirmation; it does not close the preview tab. */
  deleteFile: () => void;
}

export interface FileBrowserPreviewSlots {
  actions?:
    ReactNode | ((context: FileBrowserPreviewActionContext) => ReactNode);
  extraActions?: ReactNode;
  menuItems?: readonly FilePreviewMenuItem[];
}
