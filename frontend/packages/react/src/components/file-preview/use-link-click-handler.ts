import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';
import { useCallback, useRef } from 'react';

import type { useFilePreviewActions } from './use-file-preview-actions.js';
import type { FilePreviewFileTab } from './types.js';

/**
 * Markdown 正文里的链接点击处理。
 *
 * 用 ref 取当前的页签与动作，回调本身因此不随层级变化。它会进入正文的适配器
 * 依赖，一变就重启底座管线——层级切换时旧的那一层也会退回加载态，双层过渡
 * 等于白做。
 *
 * 只有宿主给了 `openUri` 才接管导航：传入回调后底座会阻止默认跳转，宿主没提供
 * 能力时接管等于把链接点死。
 *
 * The link click handler for Markdown bodies. A ref carries the current tab and
 * actions so the callback never changes with the level: it feeds the body's adapter
 * dependencies, and a change restarts the base's pipeline — sending the old layer
 * back to a loading state too and wasting the two-layer transition.
 *
 * Navigation is taken over only when the host supplies `openUri`: passing the callback
 * makes the base skip the default jump, so taking over without one kills the link.
 */
export function useLinkClickHandler(
  fileTab: FilePreviewFileTab | undefined,
  actions: ReturnType<typeof useFilePreviewActions>,
): MarkdownAdapterOptions['onLinkClick'] {
  const latest = useRef({ fileTab, actions });
  latest.current = { fileTab, actions };
  return useCallback<NonNullable<MarkdownAdapterOptions['onLinkClick']>>(
    (_event, link) => {
      const target = latest.current.fileTab;
      if (target) void latest.current.actions.openUri(target, link.url);
    },
    [],
  );
}
