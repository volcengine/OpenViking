import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { ReadDashboardHint } from './dashboard-resource.js';
import type { DirectorySummaryLabels } from './file-browser-labels.js';
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

export interface UseFsPreviewOptions {
  client: OvClient | null;
  uri: string | null;
  /**
   * 当前资源的类型。目录读 L0/L1 摘要，文件读正文，`unknown` 先查一次 `fs/stat`
   * 再决定。
   *
   * 传三态而不是「目录集合」：集合每次加载都会换新引用，作为 effect 依赖会让预览
   * 反复重取。三态是稳定的字符串，换引用的问题不存在。
   *
   * The type of the current resource. A directory reads its L0/L1 summary, a file reads
   * its content, and `unknown` resolves with one `fs/stat` call first.
   *
   * A three-state value rather than the directory set: the set gets a new identity on
   * every load, and as an effect dependency it would make the preview reload
   * repeatedly. The three-state value is a stable string, so that cannot happen.
   */
  resourceType?: 'directory' | 'file' | 'unknown';
  readDashboardHint?: ReadDashboardHint;
  listedUpdatedAt?: string;
  activeLevelKey?: string;
  onActiveLevelChange?: (key: string) => void;
  /** 目录两级摘要的标题。Titles of the two summary levels. */
  labels?: DirectorySummaryLabels;
  /** 字符数文案；独立 Hook 默认英文。Character count copy; standalone Hooks default to English. */
  formatCharacterCount?: (count: number) => string;
  /**
   * 打开路径上的某一级祖先。面包屑与目录页签共用它。
   * Opens one ancestor level; the breadcrumb and a directory tab share it.
   */
  openParent?: (uri: string) => Promise<void>;
  /**
   * 重新取数的代次。宿主调用刷新时递增，让同一资源的正文重新请求——内容在服务
   * 端变了而 URI 没变，只靠依赖是看不出来的。
   *
   * Reload generation. A host increments it on refresh so the same resource is read
   * again: the content changed on the server while the URI did not, which
   * dependencies alone cannot express.
   */
  generation?: number;
  requestOptions?: OvRequestOptions;
}
