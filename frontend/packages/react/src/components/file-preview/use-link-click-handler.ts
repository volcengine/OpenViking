import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';
import { useCallback, useRef } from 'react';

import type { useFilePreviewActions } from './use-file-preview-actions.js';
import type { FilePreviewDirectoryTab, FilePreviewFileTab } from './types.js';
import { levelToFileTab } from './file-preview-source.js';

/**
 * Markdown 正文里的链接点击处理。
 *
 * 用 ref 取当前的页签与动作，回调本身因此不随层级变化。它会进入正文的适配器
 * 依赖，一变就重启底座管线——层级切换时旧的那一层也会退回加载态，双层过渡
 * 等于白做。
 *
 * 文件页签只在宿主给了 `openUri` 时接管导航。目录页签始终处理同文档锚点，
 * 资源链接是否交给宿主由目录正文的链接策略决定。
 *
 * The link click handler for Markdown bodies. A ref carries the current tab and
 * actions so the callback never changes with the level: it feeds the body's adapter
 * dependencies, and a change restarts the base's pipeline — sending the old layer
 * back to a loading state too and wasting the two-layer transition.
 *
 * File navigation is taken over only with a host `openUri`. Directory tabs always
 * handle same-document anchors; their link policy decides whether resource links go to the host.
 */
export function useLinkClickHandler(
  tab: FilePreviewDirectoryTab | FilePreviewFileTab | undefined,
  actions: ReturnType<typeof useFilePreviewActions>,
  levelKey?: string,
): MarkdownAdapterOptions['onLinkClick'] {
  const fileTab =
    tab?.kind === 'directory'
      ? levelToFileTab(tab, levelKey ?? tab.levels[0].key)
      : tab;
  const latest = useRef({
    fileTab,
    actions,
    directory: tab?.kind === 'directory',
  });
  latest.current = { fileTab, actions, directory: tab?.kind === 'directory' };
  const handler = useCallback<
    NonNullable<MarkdownAdapterOptions['onLinkClick']>
  >((event, link) => {
    const url = link.url.trim();
    if (latest.current.directory && url.startsWith('#')) {
      let id;
      try {
        id = decodeURIComponent(url.slice(1));
      } catch {
        return;
      }
      const body = (event.currentTarget as HTMLElement).closest(
        '.ov-file-preview__body',
      );
      Array.from(body?.querySelectorAll('[id]') ?? [])
        .find((node) => node.id === id || node.id === `user-content-${id}`)
        ?.scrollIntoView({ block: 'start' });
      return;
    }
    const target = latest.current.fileTab;
    if (target) void latest.current.actions.openUri(target, link.url);
  }, []);
  return tab?.kind === 'directory' || fileTab?.operations?.openUri
    ? handler
    : undefined;
}
