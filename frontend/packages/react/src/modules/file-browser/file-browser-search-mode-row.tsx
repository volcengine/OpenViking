import { FileBrowserSearchModeMenu } from './file-browser-search-mode-menu.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 结果区顶部的模式行：有结果时带数量，任何时候都带模式 chip 与切换入口。
 *
 * 切换入口不能放在只于「有结果」时渲染的那一行里：语义检索报错、返回空列表、
 * 或者还没搜过时，用户正需要换成 grep/glob 再试一次，而那是唯一能换模式的地方。
 * 所以这一行在所有状态下都渲染，只有数量随结果出现。
 *
 * The mode row at the top of the results area: it carries the count when there are
 * results, and always carries the mode chip and its switch.
 *
 * The switch cannot live in a row that only renders when results exist: when semantic
 * retrieval fails, returns nothing, or has not run yet, the user needs exactly then to
 * switch to grep or glob — and this is the only place to do it. So the row renders in
 * every state, and only the count follows the results.
 */
export interface FileBrowserSearchModeRowProps {
  mode: SearchMode;
  /** 结果条数；`null` 表示当前没有可数的结果。Result count, or `null` when there is none. */
  count: number | null;
  text: ResolvedLabels;
  menuOpen: boolean;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onSelectMode: (mode: SearchMode) => void;
}

export function FileBrowserSearchModeRow({
  mode,
  count,
  text,
  menuOpen,
  onToggleMenu,
  onCloseMenu,
  onSelectMode,
}: FileBrowserSearchModeRowProps) {
  return (
    <div className="ov-file-browser-search__summary">
      {count === null ? null : (
        <>
          <span>{text.searchResultCount?.(count)}</span>
          <span aria-hidden="true">・</span>
          <span>{text.searchVia}</span>
        </>
      )}
      <span className="ov-file-browser-search__mode-chip">{mode}</span>
      <span className="ov-file-browser-search__switch-wrap">
        <button
          type="button"
          className="ov-file-browser-search__switch"
          onClick={onToggleMenu}
        >
          {text.searchSwitchMode}
        </button>
        <FileBrowserSearchModeMenu
          open={menuOpen}
          value={mode}
          text={text}
          onSelect={onSelectMode}
          onClose={onCloseMenu}
        />
      </span>
    </div>
  );
}
