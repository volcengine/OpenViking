import type { ReactNode } from 'react';
import type {
  FileTreeOperations,
  FileTreeProps,
} from '../../file-tree/types.js';

/** 目录详情及写权限，由宿主按接口结果映射。Host-mapped directory metadata. */
export interface VikingBotInteractionDirectoryInfo {
  uri: string;
  isDirectory: boolean;
  canWrite: boolean;
}

/** 权限数据由宿主加载，uri 标识所属草稿。Host-loaded permission presentation. */
export interface VikingBotInteractionDirectoryPermission {
  uri: string;
  checking?: boolean;
  info?: VikingBotInteractionDirectoryInfo;
  error?: unknown;
}

/** 目录的宿主执行能力。Host-owned directory operations. */
export interface VikingBotInteractionDirectoryOperations extends Pick<
  FileTreeOperations,
  'toggleExpand' | 'loadMore' | 'retry'
> {
  /** 刷新目录；创建成功后的刷新失败不撤销创建结果。Refresh after creation independently. */
  refresh?: (uri?: string) => Promise<void>;
  /** 创建一个直属子目录，返回接口确认成功的目录 URI。Create a direct child and return its URI. */
  createDirectory?: (
    parentUri: string,
    name: string,
    options: { signal: AbortSignal },
  ) => Promise<string>;
}

/** 目录数据和请求由宿主提供；弹窗、草稿、自动定位及状态由组件处理。 */
export type VikingBotInteractionDirectoryPicker = Pick<
  FileTreeProps,
  'rows' | 'isReady' | 'loadingFallback' | 'errorFallback' | 'emptyFallback'
> & {
  operations?: VikingBotInteractionDirectoryOperations;
  /** 宿主提供当前草稿的权限状态。Host-owned permission state. */
  permission?: VikingBotInteractionDirectoryPermission;
  onSelectionChange?: (uri: string) => void;
  /** 搜索字符串，与 onQueryChange 一起提供。Controlled directory-name search. */
  query?: string;
  onQueryChange?: (query: string) => void;
  /** 搜索或刷新期间的状态。Search or refresh pending state. */
  loading?: boolean;
  /** 加载错误；已有目录行继续显示。Load failure while retaining cached rows. */
  error?: ReactNode;
};

/** 输出目录表单，宿主通过 onSubmit 校验并保存。 */
export interface VikingBotInteractionTargetRequest {
  kind: 'target';
  question?: ReactNode;
  initialUri?: string;
  directoryPicker: VikingBotInteractionDirectoryPicker;
}

/** 目录流程可覆盖文案。Directory-flow label overrides. */
export interface VikingBotInteractionDirectoryLabels {
  directoryTarget?: string;
  directorySelected?: string;
  directorySearch?: string;
  directoryEmpty?: string;
  directoryPermissionChecking?: string;
  directoryPermissionWritable?: string;
  directoryPermissionHint?: string;
  directoryNotWritable?: string;
  directoryUnavailable?: string;
  directoryCheckFailed?: string;
  directoryCreate?: string;
  directoryCreateTitle?: string;
  directoryParent?: (uri: string) => string;
  directoryName?: string;
  directoryNameHint?: string;
  directoryNameInvalid?: string;
  directoryCreateConfirm?: string;
  directoryCreateFailed?: string;
  directoryRetry?: string;
  directoryLoadFailed?: string;
}
