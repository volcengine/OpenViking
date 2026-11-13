import { useEffect, useRef } from 'react';
import type { RefObject } from 'react';

import {
  activateInstance,
  ownsShortcut,
  registerInstance,
  unregisterInstance,
} from './search-shortcut.js';

/** 绑定 ⌘K 后交给宿主组件的事件处理器。Handlers the bound component spreads. */
export interface SearchShortcutHandlers {
  onPointerDownCapture: () => void;
  onFocusCapture: () => void;
}

/**
 * 把 ⌘K 绑到搜索弹窗的开合上，并解决同页多实例的按键归属。
 *
 * 一个页面可能挂多个 `FileBrowser`：若每个实例都响应 document 级的快捷键，一次
 * ⌘K 会让所有实例同时弹出搜索框。归属规则见 `search-shortcut.ts`。
 *
 * 返回的处理器必须挂在实例根元素上——它们记录「这个实例刚被交互过」，与按键
 * 归属用的是同一个 id。
 *
 * Binds ⌘K to the search dialog's open state and settles which instance owns the key
 * press when a page mounts several.
 *
 * A page can mount several `FileBrowser` instances: if every one answered the
 * document-level shortcut, one ⌘K would open all their dialogs at once. The ownership
 * rule lives in `search-shortcut.ts`.
 *
 * The returned handlers must go on the instance's root element — they record that
 * this instance was just interacted with, using the same id the key handler checks.
 */
export function useSearchShortcut(
  containerRef: RefObject<HTMLElement | null>,
  onToggle: () => void,
): SearchShortcutHandlers {
  const instanceId = useRef<symbol>(Symbol('file-browser'));
  const toggle = useRef(onToggle);
  toggle.current = onToggle;

  // useEffect 必要性：与 document 的键盘事件同步。快捷键没有对应的元素事件，
  // 焦点可能落在预览区或树里，只有文档级监听才收得到。回调经 ref 读取最新的
  // 开合函数，因此依赖为空；卸载时移除监听并让出实例登记。
  //
  // Syncs with the document's keyboard events. A shortcut has no corresponding element
  // event, and focus may sit in the preview or the tree, so only a document-level
  // listener sees it. The callback reads the latest toggle through a ref, hence the
  // empty dependency list; unmounting removes the listener and releases the registry
  // entry.
  useEffect(() => {
    const id = instanceId.current;
    registerInstance(id);
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        event.repeat ||
        event.altKey ||
        event.shiftKey ||
        !(event.metaKey || event.ctrlKey) ||
        event.key.toLowerCase() !== 'k'
      )
        return;
      const target = event.target;
      if (
        target instanceof Element &&
        !target.closest('.ov-file-browser-search') &&
        target.closest(
          'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="dialog"]',
        )
      )
        return;
      if (!ownsShortcut(id, containerRef.current)) return;
      event.preventDefault();
      toggle.current();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      unregisterInstance(id);
    };
  }, [containerRef]);

  const activate = () => activateInstance(instanceId.current);
  return { onPointerDownCapture: activate, onFocusCapture: activate };
}
