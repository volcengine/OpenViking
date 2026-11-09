import type { FilePreviewLabels } from '../../components/file-preview/index.js';
import type { FileTreeLabels } from '../../components/file-tree/index.js';
import type { FileBrowserLabels } from './types.js';

/** 模块外壳自己渲染的文案，其余透传给树与预览。 */
type ShellLabels = Omit<FileBrowserLabels, 'tree' | 'preview'>;

/**
 * 模块外壳的内置文案。树与预览的默认值留在各自组件里，模块不重复一份。
 * Built-in copy of the module shell. The tree and the preview keep their own
 * defaults, so the module does not duplicate them.
 */
export const DEFAULT_LABELS: Required<ShellLabels> = {
  empty: 'Select a file to preview',
  error: 'Failed to load the directory',
  retry: 'Retry',
  refresh: 'Refresh',
  searchPlaceholder: 'Search files ⌘+K...',
  searchEmpty: 'No matching resources',
  searchFailed: 'Failed to run the search',
  loading: 'Loading…',
  searchLoading: 'Searching…',
  previewMode: 'Preview',
  sourceMode: 'Source',
  copyPath: 'Copy path',
  copyContent: 'Copy content',
  download: 'Download',
  history: 'Version history',
  viewSwitch: 'Switch view',
  resizeTree: 'Resize the directory tree',
  l0Summary: 'L0 Summary',
  l1Overview: 'L1 Overview',
  summaryEmpty: 'No summary generated yet',
};

/** 解析后的外壳文案，外加透传给两个组件的文案。 */
export interface ResolvedLabels extends Required<ShellLabels> {
  tree: FileTreeLabels | undefined;
  previewLabels: FileBrowserLabels['preview'];
}

/** 逐项合并默认文案；树与预览的文案原样透传，缺省由组件自己兜底。 */
export function resolveLabels(
  labels: FileBrowserLabels | undefined,
): ResolvedLabels {
  return {
    ...DEFAULT_LABELS,
    ...labels,
    tree: labels?.tree,
    previewLabels: labels?.preview,
  };
}

/**
 * 预览组件的文案：模块自己的那几项叠在宿主透传的之上。组件那份英文默认值只在
 * 宿主完全不传时才会用到，模块不能让它在中文界面里漏出来。
 *
 * The preview component's copy: the module's own fields layered over what the host
 * passed through. The component's English defaults only apply when a host passes
 * nothing, and must not leak into a Chinese interface.
 */
export function resolvePreviewLabels(text: ResolvedLabels): FilePreviewLabels {
  return {
    // `loading` 与 `copyPath`、`download` 一样是模块自己渲染的文案，必须带上：
    // 漏掉它，预览的加载态就会走组件里的英文兜底，中文界面里冒出「Loading…」。
    //
    // `loading` belongs here like `copyPath` and `download`: it is copy the module
    // renders itself. Leaving it out sends the preview's loading state to the
    // component's English fallback, which surfaces as "Loading…" in a Chinese UI.
    loading: text.loading,
    copyPath: text.copyPath,
    download: text.download,
    ...text.previewLabels,
  };
}
