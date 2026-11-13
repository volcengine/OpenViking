import { useMemo } from 'react';
import type { FilePreviewTab } from '../../components/file-preview/index.js';
import { resolveLinkTarget } from './fs-link-target.js';

/**
 * 给预览页签接上链接导航。只给 Markdown 页签补 `operations.openUri`：补上之后
 * 组件会阻止默认跳转，所以站内资源走模块自己的导航（选中、展开、切摘要层级），
 * 站外链接在新窗口打开，不会被点死。
 *
 * Wires link navigation into the preview tabs. Supplying `operations.openUri`
 * makes the component skip the default jump, so in-app resources go through the
 * module's own navigation (select, expand, switch summary level) and external
 * links open in a new window rather than going dead.
 */
export function usePreviewTabs<
  T extends { previewTabs: readonly FilePreviewTab[] | null },
>(browser: T, openResource: (uri: string) => Promise<boolean>): T {
  const { previewTabs } = browser;
  return useMemo(
    () => ({
      ...browser,
      previewTabs:
        previewTabs?.map((tab) => {
          if (tab.kind === 'content') return tab;
          const openUri = async (raw: string) => {
            const target = resolveLinkTarget(
              raw,
              // 目录摘要里的相对链接以目录本身为基准。
              // Relative links in a directory summary resolve against the directory.
              tab.kind === 'directory' ? `${tab.key}/` : tab.key,
            );
            if (target?.kind === 'resource') await openResource(target.uri);
            else if (target)
              window.open(target.url, '_blank', 'noopener,noreferrer');
          };
          return { ...tab, operations: { ...tab.operations, openUri } };
        }) ?? null,
    }),
    [browser, previewTabs, openResource],
  );
}
