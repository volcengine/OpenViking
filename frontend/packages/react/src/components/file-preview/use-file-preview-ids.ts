import { useId } from 'react';

/**
 * 页签与面板的 DOM id 生成器，带实例前缀。
 *
 * 只用宿主给的 `tab.key` 拼 id，两个预览同时挂载时会产生重复 id，`aria-controls`
 * 与 `aria-labelledby` 因此可能指向另一个实例的元素。`useId` 给出每个实例唯一
 * 的前缀，页签行与面板两侧共用同一份。
 *
 * DOM id generators for tabs and panels, prefixed per instance.
 *
 * Building ids from the host's `tab.key` alone collides when two previews are
 * mounted at once, so `aria-controls` and `aria-labelledby` could point at the
 * other instance's elements. `useId` supplies a unique prefix per instance, shared
 * by the tab row and the panels.
 */
export function useFilePreviewIds(activeKey: string) {
  const instanceId = useId();
  const tabId = (key: string) => `${instanceId}-tab-${key}`;
  const panelId = (key: string) => `${instanceId}-panel-${key}`;
  return {
    tabId,
    panelId,
    /** 面板的公共属性：id、role 与 aria 关联，随当前页签变化。 */
    panelProps: {
      id: panelId(activeKey),
      role: 'tabpanel' as const,
      'aria-labelledby': tabId(activeKey),
    },
  };
}
