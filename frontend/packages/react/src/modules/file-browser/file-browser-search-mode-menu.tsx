import { useCallback, useEffect } from 'react';
import type { RefObject } from 'react';

import { SEARCH_MODES } from './search-types.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 模式下拉的内容：四种检索方式各一行，带一句说明，选中项打勾。
 *
 * 浮层本身由结果区的 `Popover` 提供——它挂到 `document.body` 并用视口坐标定位，
 * 结果区裁掉的溢出不会波及菜单。这里只画内容，另外补两件事：挂载后把焦点交给
 * 调用方（键盘打开时移进第一项），以及点击菜单外时关闭。
 *
 * The mode menu's content: one row per retrieval mode with a one-line description and a
 * check on the current one.
 *
 * The surface comes from the result area's `Popover`, which portals into `document.body`
 * and positions in viewport coordinates, so the results area's clipping cannot reach it.
 * This draws the content and handles two things: handing the node to the caller after
 * mount (so a keyboard opening can move focus to the first item), and closing on a click
 * outside.
 */
export interface FileBrowserSearchModeMenuProps {
  value: SearchMode;
  text: ResolvedLabels;
  menuRef?: RefObject<HTMLDivElement | null>;
  onSelect: (mode: SearchMode) => void;
  /** 菜单挂载后调用，供调用方移动焦点。 Called after mount so the caller can focus. */
  onMounted?: () => void;
  /** 点击菜单外部时调用。 Called on a click outside the menu. */
  onDismiss: () => void;
}

export function FileBrowserSearchModeMenu({
  value,
  text,
  menuRef,
  onSelect,
  onMounted,
  onDismiss,
}: FileBrowserSearchModeMenuProps) {
  // 回调 ref 必须稳定：每次渲染新建函数会让 React 先 `ref(null)` 再 `ref(node)`，
  // 而 `onMounted` 在挂载回调里移焦点，重复触发会让焦点反复跳动。
  //
  // The callback ref must be stable: a fresh function per render makes React call
  // `ref(null)` then `ref(node)`, and since `onMounted` moves focus from that callback,
  // repeated firing would bounce focus around.
  const attachMenu = useCallback(
    (node: HTMLDivElement | null) => {
      if (menuRef) menuRef.current = node;
      if (node) onMounted?.();
    },
    [menuRef, onMounted],
  );
  // useEffect 必要性：与 document 的指针与键盘事件同步。浮层渲染在 portal 里，
  // DOM 上不是触发按钮的子孙，点击外部关闭必须监听整个文档——只监听自己的节点
  // 收不到外部点击。Esc 也在这里处理：菜单容器是 `role="group"`，给它挂键盘
  // 监听既不合语义，也拦不住焦点在按钮上时的冒泡。捕获阶段先于弹窗的 Esc 处理，
  // 一次 Esc 只关菜单。卸载时移除。
  //
  // Syncs with the document's pointer and keyboard events. The surface renders in a
  // portal, so it is not a DOM descendant of the trigger; closing on an outside click
  // needs a document-level listener. Escape is handled here too: the menu is a
  // `role="group"`, so a key listener on it is both wrong semantically and unable to
  // intercept events while focus sits on a button. Capture runs before the dialog's own
  // Escape handler, so one Escape closes the menu only. Removed on unmount.
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      if (menuRef?.current?.contains(event.target)) return;
      onDismiss();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      event.stopPropagation();
      onDismiss();
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [menuRef, onDismiss]);

  return (
    <div
      ref={attachMenu}
      className="ov-file-browser-search__menu"
      role="group"
      aria-label={text.searchSwitchMode}
    >
      {SEARCH_MODES.map((mode) => (
        <button
          key={mode}
          type="button"
          aria-pressed={mode === value}
          className="ov-file-browser-search__menu-item"
          onClick={() => onSelect(mode)}
        >
          <span className="ov-file-browser-search__menu-text">
            <span className="ov-file-browser-search__menu-name">{mode}</span>
            <span className="ov-file-browser-search__menu-hint">
              {text.searchModeHints[mode]}
            </span>
          </span>
          {mode === value ? (
            <svg
              aria-hidden="true"
              width={14}
              height={14}
              viewBox="0 0 16 16"
              fill="none"
            >
              <path
                d="M3 8.5 6.5 12 13 4.5"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : null}
        </button>
      ))}
    </div>
  );
}
