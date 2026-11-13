import { Button } from '../../shared/primitives/button/button.js';
import SearchIcon from '../../../../icons/svg/scope-switcher/search.svg?react';
import type { ResolvedLabels } from './file-tree-row-context.js';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import type { FileTreeProps } from './types.js';

type ToolbarProps = Pick<
  FileTreeProps,
  'search' | 'refresh' | 'loading' | 'className'
> & {
  text: Pick<ResolvedLabels, 'search' | 'refresh'>;
  refreshing: boolean;
  onSearch?: () => void;
  onRefresh?: () => void;
};

/** Optional host-driven search and refresh controls; no data fetching here. */
export function FileTreeToolbar({
  search,
  refresh,
  loading,
  text,
  refreshing,
  onSearch,
  onRefresh,
  className,
}: ToolbarProps) {
  if (!search && !refresh) return null;
  const searchDisabled =
    !onSearch || (typeof search === 'object' && search.disabled);
  const refreshDisabled =
    !onRefresh || (typeof refresh === 'object' && refresh.disabled);
  const busy =
    loading || refreshing || (typeof refresh === 'object' && refresh.loading);

  return (
    <div
      className={['ov-file-tree__controls', className]
        .filter(Boolean)
        .join(' ')}
    >
      {search ? (
        <Button
          size="default"
          type="default"
          htmlType="button"
          className="ov-file-tree__search"
          disabled={Boolean(searchDisabled)}
          onClick={onSearch}
          title={text.search}
        >
          <SearchIcon width={16} height={16} aria-hidden="true" />
          <span className="ov-file-tree__search-label">{text.search}</span>
        </Button>
      ) : null}
      {refresh ? (
        <Tooltip
          content={text.refresh}
          disabled={Boolean(refreshDisabled || busy)}
        >
          <Button
            className="ov-file-tree__refresh"
            aria-label={text.refresh}
            aria-busy={Boolean(busy)}
            loading={Boolean(busy)}
            disabled={Boolean(refreshDisabled)}
            onClick={onRefresh}
            icon={
              <svg
                aria-hidden="true"
                width={14}
                height={14}
                viewBox="0 0 16 16"
                fill="none"
              >
                <path
                  d="M13 6a5.2 5.2 0 1 0 .15 3.5M13 2.5V6H9.5"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            }
          />
        </Tooltip>
      ) : null}
    </div>
  );
}
