import { resolveLocale, type Locale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = { 'en-US': enUS, 'zh-CN': zhCN };

import type { FilePreviewLabels } from '../../components/file-preview/index.js';
import type { FileTreeLabels } from '../../components/file-tree/index.js';
import type { FileBrowserLabels } from './types.js';

/** 模块外壳自己渲染的文案，其余透传给树与预览。 */
export type ShellLabels = Omit<
  FileBrowserLabels,
  'tree' | 'preview' | 'entries' | 'dashboard'
>;

/**
 * 模块外壳的内置文案。树与预览的默认值留在各自组件里，模块不重复一份。
 * Built-in copy of the module shell. The tree and the preview keep their own
 * defaults, so the module does not duplicate them.
 */

/** 解析后的外壳文案，外加透传给两个组件的文案。 */
export interface ResolvedLabels extends Required<ShellLabels> {
  tree: FileTreeLabels | undefined;
  previewLabels: FileBrowserLabels['preview'];
}

/** 逐项合并默认文案；树与预览的文案原样透传，缺省由组件自己兜底。 */
export function resolveLabels(
  labels: FileBrowserLabels | undefined,
  locale: Locale,
): ResolvedLabels {
  return {
    ...resolveLocale(LOCALES, locale, labels),
    tree: labels?.tree,
    previewLabels: labels?.preview,
  };
}

/** Shared shell labels followed by explicit preview overrides. */
export function resolvePreviewLabels(text: ResolvedLabels): FilePreviewLabels {
  return {
    loading: text.loading,
    copyPath: text.copyPath,
    download: text.download,
    ...text.previewLabels,
  };
}

export const DEFAULT_LABELS: Required<ShellLabels> = enUS;

/** Summary titles are derived from the module resource, including host overrides. */
export function resolveSummaryLabels(
  locale: Locale,
  labels?: FileBrowserLabels,
) {
  const text = resolveLabels(labels, locale);
  return {
    l0: text.l0Summary,
    l1: text.l1Overview,
    failure: text.summaryFailed,
  };
}
