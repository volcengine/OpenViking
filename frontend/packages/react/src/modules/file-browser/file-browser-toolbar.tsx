import { Tooltip } from '../../shared/components/tooltip/index.js';
import { RefreshIcon, SearchIcon } from './file-browser-icons.js';

/**
 * 搜索行：搜索框与刷新按钮。两者都是模块自己的，宿主入口在上一行的
 * `toolbarActions` 里。
 *
 * The search row: the search box and the refresh button. Both belong to the module;
 * host entries live in `toolbarActions` on the row above.
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
