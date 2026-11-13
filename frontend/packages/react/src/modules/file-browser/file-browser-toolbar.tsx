import SearchIcon from '../../../../icons/svg/scope-switcher/search.svg?react';
import FilterIcon from '../../../../icons/svg/dashboards/severity-filter.svg?react';

/**
 * 左栏的搜索行：一个搜索框形态的入口、内嵌的高级筛选按钮，以及独立的刷新。
 *
 * 与 `FileTreeToolbar` 分开而不是复用：那个是 `FileTree` 的公开工具栏，两段式
 * （搜索按钮 + 刷新按钮），改成设计稿的三段式会波及 `FileTree` 的所有使用方。
 * 这里的形态只属于文件浏览模块。
 *
 * The search row of the left column: a search-box-shaped entry with an embedded
 * advanced-filter button and a separate refresh.
 *
 * It is separate from `FileTreeToolbar` rather than reusing it: that one is
 * `FileTree`'s public toolbar with two segments (search and refresh), and reshaping it
 * into the design's three would affect every `FileTree` consumer. This shape belongs
 * to the file browser alone.
 */
export interface FileBrowserToolbarProps {
  /** 刷新是否进行中。Whether a refresh is running. */
  refreshing: boolean;
  /** 刷新是否可用。Whether refresh is available. */
  canRefresh: boolean;
  /** 搜索入口文案。Search entry label. */
  placeholder: string;
  /** 高级筛选按钮的无障碍名称。Accessible name of the advanced-filter button. */
  advancedLabel: string;
  /** 刷新按钮的无障碍名称。Accessible name of the refresh control. */
  refreshLabel: string;
  /** 高级面板当前是否展开。Whether the advanced panel is open. */
  advancedOpen?: boolean;
  onSearch: () => void;
  onAdvanced: () => void;
  onRefresh: () => void;
}

export function FileBrowserToolbar({
  refreshing,
  canRefresh,
  placeholder,
  advancedLabel,
  refreshLabel,
  advancedOpen = false,
  onSearch,
  onAdvanced,
  onRefresh,
}: FileBrowserToolbarProps) {
  return (
    <div className="ov-file-browser__toolbar">
      <div className="ov-file-browser__search-entry">
        <button
          type="button"
          className="ov-file-browser__search-trigger"
          onClick={onSearch}
        >
          <SearchIcon width={14} height={14} aria-hidden="true" />
          <span className="ov-file-browser__search-trigger-label">
            {placeholder}
          </span>
          <kbd className="ov-file-browser__search-kbd">⌘K</kbd>
        </button>
        <button
          type="button"
          className="ov-file-browser__advanced-trigger"
          aria-label={advancedLabel}
          aria-expanded={advancedOpen}
          onClick={onAdvanced}
        >
          <FilterIcon width={14} height={14} aria-hidden="true" />
        </button>
      </div>
      <button
        type="button"
        className="ov-file-browser__refresh"
        aria-label={refreshLabel}
        aria-busy={refreshing}
        disabled={!canRefresh || refreshing}
        onClick={onRefresh}
      >
        <svg
          aria-hidden="true"
          width={14}
          height={14}
          viewBox="0 0 16 16"
          fill="none"
          className={refreshing ? 'ov-file-browser__refresh-spin' : undefined}
        >
          <path
            d="M13 6a5.2 5.2 0 1 0 .15 3.5M13 2.5V6H9.5"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  );
}
