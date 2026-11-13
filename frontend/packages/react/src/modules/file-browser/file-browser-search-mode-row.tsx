import { useCallback, useRef, useState } from 'react';
import type { KeyboardEvent, RefObject } from 'react';

import { Popover } from '../../shared/primitives/popover/popover.js';
import { FileBrowserSearchModeMenu } from './file-browser-search-mode-menu.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 结果区顶部的模式行：数量、模式 chip、检索方式说明与切换入口。
 *
 * 顺序与设计稿一致：先给数量，再给当前模式，最后才是切换入口。
 *
 * 下拉挂在「切换」按钮上，由 `Popover` 渲染到 `document.body`：结果区为了固定
 * 头部会裁掉溢出，菜单留在原地时在窄屏会被裁掉大半。菜单 portal 出去后不在弹窗
 * 的 DOM 子树里，`Popover` 会把它登记进弹窗的焦点范围，Tab 因此仍能进入。
 *
 * 键盘打开（Enter、Space、↓ 进第一项，↑ 进最后一项）时焦点移进菜单；关闭时若
 * 焦点还在菜单里就还给「切换」按钮——否则 portal 的菜单虽然能被 Tab 到，键盘
 * 用户仍要多按几次才知道菜单开了。
 *
 * The mode row above the results: the count, the mode chip, what the mode retrieves
 * and the switch.
 *
 * The order follows the design: the count first, then the current mode, then the
 * switch.
 *
 * The menu hangs off the switch button and is rendered by `Popover` into
 * `document.body`: the results area clips its overflow so the header can stay put, and a
 * menu left in place would lose most of itself on a narrow viewport. Once portaled it is
 * outside the dialog's DOM subtree, so `Popover` registers it into the dialog's focus
 * scope and Tab can still reach it.
 *
 * Opening from the keyboard (Enter, Space and ↓ land on the first item, ↑ on the last)
 * moves focus into the menu; closing returns it to the switch when it was inside the
 * menu. Without that, the portaled menu would be reachable but a keyboard user would
 * have no sign it had opened.
 */
export interface FileBrowserSearchModeRowProps {
  mode: SearchMode;
  count: number;
  text: ResolvedLabels;
  /** 「切换」不在时的焦点去处；选完模式会立刻重新检索，本行在加载态里不渲染。 */
  fallbackFocusRef?: RefObject<HTMLElement | null>;
  onSelectMode: (mode: SearchMode) => void;
}

export function FileBrowserSearchModeRow({
  mode,
  count,
  text,
  fallbackFocusRef,
  onSelectMode,
}: FileBrowserSearchModeRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  // 菜单内容在 `Popover` 里，节点要等它挂载后才拿得到；用 ref 交给它的回调，
  // 不额外存 state——焦点只在打开那一刻移动一次。
  //
  // The menu lives inside `Popover` and only exists after it mounts; hand the node over
  // through a ref rather than extra state, since focus moves exactly once per opening.
  const menuRef = useRef<HTMLDivElement | null>(null);
  const pendingFocus = useRef<'first' | 'last' | null>(null);

  const focusPendingItem = useCallback(() => {
    const edge = pendingFocus.current;
    if (!edge) return;
    pendingFocus.current = null;
    const items = menuRef.current?.querySelectorAll<HTMLButtonElement>(
      'button:not(:disabled)',
    );
    const target = edge === 'first' ? items?.[0] : items?.[items.length - 1];
    target?.focus();
  }, []);

  // 焦点去处取决于菜单怎么关的：
  //
  // - 选中某个模式时，`onSelectMode` 会立刻重新检索，结果区在加载态里不渲染模式行，
  //   「切换」按钮随这一行卸载，`focus()` 会落空并把焦点丢给 `body`。所以这里交给
  //   搜索输入框。
  // - Esc 与点击外部时这一行还在，焦点回到「切换」；焦点本来就不在菜单里（用户点
  //   了别处）则不动，免得把焦点从刚点的地方抢走。
  //
  // Where focus goes depends on how the menu closed:
  //
  // - Picking a mode triggers a new search at once, and the results area renders no mode
  //   row while loading, so the switch unmounts with that row and `focus()` would land
  //   nowhere, dropping focus to `body`. The search input takes it instead.
  // - Escape and outside clicks leave the row in place, so focus returns to the switch —
  //   unless it was never inside the menu (the user clicked elsewhere), where moving it
  //   would steal focus from where they just clicked.
  const close = useCallback(
    (focusTarget: 'switch' | 'fallback' = 'switch') => {
      const inside =
        !!menuRef.current?.contains(document.activeElement) ||
        pendingFocus.current !== null;
      pendingFocus.current = null;
      setMenuOpen(false);
      if (focusTarget === 'fallback') {
        fallbackFocusRef?.current?.focus();
        return;
      }
      if (inside) anchorRef.current?.focus();
    },
    [fallbackFocusRef],
  );

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'Escape' && menuOpen) {
      event.preventDefault();
      close();
      return;
    }
    if (!['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
    if (menuOpen) return;
    // Enter 与 Space 的默认行为是点击，这里显式接管，焦点移动只发生一次。
    // Enter and Space would otherwise click; take over so focus moves exactly once.
    event.preventDefault();
    pendingFocus.current = event.key === 'ArrowUp' ? 'last' : 'first';
    setMenuOpen(true);
  };

  return (
    <div className="ov-file-browser-search__summary">
      <span>{text.searchResultCount?.(count)}</span>
      <span aria-hidden="true">・</span>
      <span className="ov-file-browser-search__mode-chip">{mode}</span>
      <span>{text.searchVia}</span>
      <Popover
        content={
          <FileBrowserSearchModeMenu
            value={mode}
            text={text}
            menuRef={menuRef}
            onMounted={focusPendingItem}
            onSelect={(next) => {
              close('fallback');
              onSelectMode(next);
            }}
            onDismiss={close}
          />
        }
        open={menuOpen}
        placement="bottom"
        alignment="end"
        anchorRef={anchorRef}
        trigger="click"
        className="ov-file-browser-search__menu-surface"
        onOpenChange={(next) => {
          if (next) setMenuOpen(true);
          else close();
        }}
      >
        <button
          ref={anchorRef}
          type="button"
          className="ov-file-browser-search__switch"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onKeyDown={onKeyDown}
        >
          {text.searchSwitchMode}
        </button>
      </Popover>
    </div>
  );
}
