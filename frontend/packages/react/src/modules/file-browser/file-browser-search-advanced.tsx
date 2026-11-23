import SearchAdvancedCloseIcon from '../../../../icons/svg/file-browser/search-advanced-close.svg?react';
import type { OvRequestOptions } from '../../client/types.js';
import { Checkbox } from '../../shared/primitives/checkbox/checkbox.js';
import type { ScopeOption, SearchFilters, SearchMode } from './search-types.js';
import { EMPTY_FILTERS } from './search-types.js';
import { FILTER_AVAILABILITY } from './search-model.js';
import { FileBrowserSearchScopes } from './file-browser-search-scopes.js';
import { FileBrowserSearchTime } from './file-browser-search-time.js';
import type { Locale } from '../../shared/locale.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 高级筛选面板：目录、内容层级、时间，外加忽略大小写。
 *
 * 每个分区按当前检索方式启用或禁用。禁用的分区保留可见但置灰，标题旁说明原因
 * ——设计稿画了这些控件，直接藏起来会让人以为漏做了，而置灰至少能看出「这里
 * 本来有东西，只是当前不能用」。
 *
 * The advanced filter panel: directory, content level and time, plus ignore-case.
 *
 * Each group is enabled or disabled by the current retrieval mode. A disabled group
 * stays visible but dimmed, with the reason beside its title: the design draws these
 * controls, so hiding them reads as an omission, while dimming at least shows
 * something exists here and is simply unavailable now.
 */
export interface FileBrowserSearchAdvancedProps {
  mode: SearchMode;
  filters: SearchFilters;
  scopes: ScopeOption[];
  requestOptions?: OvRequestOptions;
  locale: Locale;
  text: ResolvedLabels;
  onChange: (filters: SearchFilters) => void;
  onClose: () => void;
}

export function FileBrowserSearchAdvanced({
  mode,
  filters,
  scopes,
  requestOptions,
  locale,
  text,
  onChange,
  onClose,
}: FileBrowserSearchAdvancedProps) {
  const availability = FILTER_AVAILABILITY[mode];

  return (
    <aside
      className="ov-file-browser-search__advanced"
      aria-label={text.searchAdvanced}
    >
      <div className="ov-file-browser-search__advanced-head">
        <h3 className="ov-file-browser-search__advanced-title">
          {text.searchAdvanced}
        </h3>
        <button
          type="button"
          className="ov-file-browser-search__advanced-clear"
          onClick={() => onChange({ ...EMPTY_FILTERS })}
        >
          {text.searchClear}
        </button>
        <button
          type="button"
          className="ov-file-browser-search__advanced-close"
          aria-label={text.searchAdvanced}
          onClick={onClose}
        >
          <SearchAdvancedCloseIcon aria-hidden="true" />
        </button>
      </div>

      <div className="ov-file-browser-search__advanced-body">
        <FileBrowserSearchScopes
          scopes={scopes}
          requestOptions={requestOptions}
          filters={filters}
          availability={availability}
          text={text}
          onChange={onChange}
        />
        <FileBrowserSearchTime
          filters={filters}
          availability={availability}
          locale={locale}
          text={text}
          onChange={onChange}
        />
        {availability.caseInsensitive ? (
          <Checkbox
            checked={filters.caseInsensitive}
            onChange={(next) => onChange({ ...filters, caseInsensitive: next })}
          >
            {text.searchIgnoreCase}
          </Checkbox>
        ) : null}
      </div>
    </aside>
  );
}
