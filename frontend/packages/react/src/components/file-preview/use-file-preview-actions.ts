import { useAnalyticsRef } from '../../shared/analytics/context.js';
import { useRef } from 'react';

import type { FilePreviewFileTab, FilePreviewTab } from './types.js';

/** 执行能力与成功通知的配对，失败时静默且不通知。 */
interface OperationPair<T> {
  execute?: (value: T) => Promise<void>;
  notify?: (value: T) => void;
}

/** 复制由组件完成，成功后通知；复制失败不通知。 */
async function copyText(
  value: string,
  notify?: (text: string) => void,
  report?: (copied: boolean) => void,
): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    report?.(false);
    return false;
  }
  report?.(true);
  notify?.(value);
  return true;
}

/**
 * 文件页签的操作执行与成功通知。
 *
 * 沿用文件树的约定：`operations` 里的异步方法成功后才触发对应的 `on…`
 * 回调，失败静默；同一操作在飞时忽略重复调用。
 *
 * Executes file-tab operations and reports success. Follows the file-tree
 * convention: the matching `on…` callback fires only after the async operation
 * resolves, failures stay silent, and repeated calls are ignored while one is
 * still in flight.
 */
export function useFilePreviewActions() {
  const analytics = useAnalyticsRef();
  const pending = useRef(new Set<string>());

  async function run<T>(
    key: string,
    { execute, notify }: OperationPair<T>,
    value: T,
  ) {
    if (!execute || pending.current.has(key)) return;
    pending.current.add(key);
    try {
      await execute(value);
    } catch {
      return;
    } finally {
      pending.current.delete(key);
    }
    notify?.(value);
  }

  return {
    // 目录页签也有路径行，面包屑同样可以沿祖先打开；内容页签没有路径，忽略。
    // A directory tab has a path row too, so its breadcrumb opens ancestors the same
    // way; a content tab has no path and is ignored.
    openParent: (tab: FilePreviewTab, uri: string) =>
      tab.kind === 'content'
        ? Promise.resolve()
        : run(
            `openParent:${tab.key}`,
            { execute: tab.operations?.openParent, notify: tab.onOpenParent },
            uri,
          ),
    openUri: (tab: FilePreviewFileTab, uri: string) =>
      run(
        `openUri:${tab.key}`,
        { execute: tab.operations?.openUri, notify: tab.onOpenUri },
        uri,
      ),
    copyPath: async (tab: FilePreviewTab, path: string) => {
      analytics.current?.emit('file_preview_copy_path_click', () => ({}));
      return copyText(path, tab.onCopyPath, (copied) =>
        analytics.current?.emit(
          copied
            ? 'file_preview_copy_path_success'
            : 'file_preview_copy_path_fail',
          () => ({}),
        ),
      );
    },
  };
}
