import { useRef, useState } from 'react';

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
 * 头部会裁掉溢出，菜单留在原地时在窄屏会被裁掉大半。
 *
 * The mode row above the results: the count, the mode chip, what the mode retrieves
 * and the switch.
 *
 * The order follows the design: the count first, then the current mode, then the
 * switch.
 *
 * The menu hangs off the switch button and is rendered by `Popover` into
 * `document.body`: the results area clips its overflow so the header can stay put, and a
 * menu left in place would lose most of itself on a narrow viewport.
 */
export interface FileBrowserSearchModeRowProps {
  mode: SearchMode;
  count: number;
  text: ResolvedLabels;
  onSelectMode: (mode: SearchMode) => void;
}

export function FileBrowserSearchModeRow({
  mode,
  count,
  text,
  onSelectMode,
}: FileBrowserSearchModeRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
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
            onSelect={(next) => {
              setMenuOpen(false);
              onSelectMode(next);
            }}
          />
        }
        open={menuOpen}
        placement="bottom"
        alignment="end"
        anchorRef={anchorRef}
        trigger="click"
        className="ov-file-browser-search__menu-surface"
        onOpenChange={setMenuOpen}
      >
        <button
          ref={anchorRef}
          type="button"
          className="ov-file-browser-search__switch"
        >
          {text.searchSwitchMode}
        </button>
      </Popover>
    </div>
  );
}
