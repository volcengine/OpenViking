import { useId } from 'react';

import { Checkbox } from '../../shared/components/checkbox/checkbox.js';
import { Radio } from '../../shared/components/radio/radio.js';
import type { ScopeOption, SearchFilters } from './search-types.js';
import type { FilterAvailability } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 高级面板的目录分区：一列可勾选的目录，语义检索多选、grep/glob 单选。
 *
 * 多选切到单选时只留第一个：grep/glob 只接受一个起始 URI，留着后面几个会让用户
 * 以为它们也生效了。
 *
 * The directory group of the advanced panel: a column of selectable scopes, multi-select
 * for semantic modes and single for grep/glob.
 *
 * Switching to single keeps only the first: grep/glob take one start URI, and keeping
 * the rest would imply they still apply.
 */
export interface FileBrowserSearchScopesProps {
  scopes: ScopeOption[];
  filters: SearchFilters;
  availability: FilterAvailability;
  text: ResolvedLabels;
  onChange: (filters: SearchFilters) => void;
}

export function FileBrowserSearchScopes({
  scopes,
  filters,
  availability,
  text,
  onChange,
}: FileBrowserSearchScopesProps) {
  // 单选模式下 name 必须逐实例唯一，否则同页两个 FileBrowser 的目录单选会互斥。
  //
  // In single-select mode the name must be unique per instance, or the two FileBrowser
  // components on one page would share one radio group.
  const scopeName = useId();

  const toggle = (uri: string) => {
    const selected = filters.scopes.includes(uri);
    if (availability.scope === 'single') {
      onChange({ ...filters, scopes: selected ? [] : [uri] });
      return;
    }
    onChange({
      ...filters,
      scopes: selected
        ? filters.scopes.filter((item) => item !== uri)
        : [...filters.scopes, uri],
    });
  };

  return (
    <section className="ov-file-browser-search__group">
      <h4 className="ov-file-browser-search__group-title">
        {text.searchScopes}
      </h4>
      {scopes.length === 0 ? (
        <p className="ov-file-browser-search__group-empty">
          {text.searchScopesUnsupported}
        </p>
      ) : (
        <ul className="ov-file-browser-search__options">
          {scopes.map((option) => {
            const selected = filters.scopes.includes(option.uri);
            return (
              <li key={option.uri}>
                {availability.scope === 'single' ? (
                  <Radio
                    name={scopeName}
                    checked={selected}
                    onChange={() => toggle(option.uri)}
                  >
                    <span className="ov-file-browser-search__option-uri">
                      {option.uri}
                    </span>
                  </Radio>
                ) : (
                  <Checkbox
                    checked={selected}
                    onChange={() => toggle(option.uri)}
                  >
                    <span className="ov-file-browser-search__option-uri">
                      {option.uri}
                    </span>
                  </Checkbox>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
