import { Tooltip } from '../../shared/components/tooltip/index.js';
import { RefreshIcon, SearchIcon } from './file-browser-icons.js';

/**
 * 搜索行：搜索框与刷新按钮。内置的连接与导入入口位于上一行。
 *
 * The search row: the search box and refresh button. Built-in connection and import
 * entries occupy the row above.
 */
export interface FileBrowserToolbarProps {
  searchId: string;
  query: string;
  refreshing: boolean;
  canRefresh: boolean;
  placeholder: string;
  refreshLabel: string;
  onQueryChange: (value: string) => void;
  onRefresh: () => void;
}

export function FileBrowserToolbar({
  searchId,
  query,
  refreshing,
  canRefresh,
  placeholder,
  refreshLabel,
  onQueryChange,
  onRefresh,
}: FileBrowserToolbarProps) {
  return (
    <div className="ov-file-browser__toolbar">
      <label className="ov-file-browser__search" htmlFor={searchId}>
        <span className="ov-file-browser__search-icon">
          <SearchIcon />
        </span>
        <input
          id={searchId}
          type="search"
          className="ov-file-browser__search-input"
          placeholder={placeholder}
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </label>
      {/* 纯图标按钮用 Tooltip 说明用途：原生 title 的延迟与样式都不受控。 */}
      <Tooltip content={refreshLabel}>
        <button
          type="button"
          className={[
            'ov-file-browser__refresh',
            refreshing ? 'ov-file-browser__refresh--spinning' : '',
          ]
            .filter(Boolean)
            .join(' ')}
          aria-label={refreshLabel}
          disabled={!canRefresh}
          onClick={onRefresh}
        >
          <RefreshIcon />
        </button>
      </Tooltip>
    </div>
  );
}
