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
 * This draws the content, hands the node to the caller after mount (so a keyboard opening
 * can move focus to the first item) and closes on Escape. Outside clicks stay with
 * `Popover`, which already excludes both the surface and the trigger.
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
  // 点击外部关闭交给 `Popover`：它的点击态已经同时排除浮层与触发按钮，点「切换」
  // 才能正常切换关闭。这里再挂一份只排除菜单的 `pointerdown` 监听，会把「切换」
  // 当成外部点击——先关掉，随后的 click 又把它打开，菜单永远关不上。
  //
  // Outside clicks belong to `Popover`, whose click trigger already excludes both the
  // surface and its trigger, so clicking the switch toggles it closed. A second
  // `pointerdown` listener here that excludes only the menu would treat the switch as an
  // outside click — closing on pointerdown and reopening on the following click, so the
  // menu could never be dismissed by clicking it again.
  //
  // useEffect 必要性：Esc 要监听整个文档。菜单容器是 `role="group"`，给它挂键盘
  // 监听既不合语义，也拦不住焦点在按钮上时的冒泡。捕获阶段先于弹窗的 Esc 处理，
  // 一次 Esc 只关菜单。卸载时移除。
  //
  // The Escape listener has to sit on the document. The menu is a `role="group"`, so a
  // key listener on it is both wrong semantically and unable to intercept events while
  // focus sits on a button. Capture runs before the dialog's own Escape handler, so one
  // Escape closes the menu only. Removed on unmount.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      event.stopPropagation();
      onDismiss();
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [onDismiss]);

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
