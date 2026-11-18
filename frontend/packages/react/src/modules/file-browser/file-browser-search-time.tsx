import { useId } from 'react';

import { Checkbox } from '../../shared/primitives/checkbox/checkbox.js';
import { DatePicker } from '../../shared/primitives/date-picker/date-picker.js';
import { Radio } from '../../shared/primitives/radio/radio.js';
import type { SearchFilters } from './search-types.js';
import type { FilterAvailability } from './search-types.js';
import type { Locale } from '../../shared/locale.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 高级面板的内容层级与时间两个分区：三级内容的复选，以及按创建 / 更新时间的
 * 单选加起止日期。
 *
 * The content-level and time groups of the advanced panel: three content levels as
 * checkboxes, and a created/updated radio pair with a date range.
 */
export interface FileBrowserSearchTimeProps {
  filters: SearchFilters;
  availability: FilterAvailability;
  /** 当前界面语言，交给日期控件的 `Intl` 格式化。UI language for the date control's `Intl` formatting. */
  locale: Locale;
  text: ResolvedLabels;
  onChange: (filters: SearchFilters) => void;
}

const LEVELS = [0, 1, 2];

export function FileBrowserSearchTime({
  filters,
  availability,
  locale,
  text,
  onChange,
}: FileBrowserSearchTimeProps) {
  // radio 的 name 必须逐实例唯一：同页面挂两个 FileBrowser 时，写死的 name 会让
  // 两个弹窗里的「按创建时间」互斥，选中一个会取消另一个的选中态。
  //
  // The radios' name must be unique per instance: with two FileBrowser components on one
  // page, a hard-coded name makes "by creation time" in the two dialogs mutually
  // exclusive, and picking one clears the other.
  const timeFieldName = useId();

  const toggleLevel = (level: number) => {
    onChange({
      ...filters,
      levels: filters.levels.includes(level)
        ? filters.levels.filter((item) => item !== level)
        : [...filters.levels, level].sort(),
    });
  };

  return (
    <>
      <section className="ov-file-browser-search__group">
        <h4 className="ov-file-browser-search__group-title">
          {text.searchLevels}
          {!availability.levels ? (
            <span className="ov-file-browser-search__group-note">
              {text.searchFilterUnsupported}
            </span>
          ) : null}
        </h4>
        <div className="ov-file-browser-search__row">
          {LEVELS.map((level) => (
            <Checkbox
              key={level}
              checked={filters.levels.includes(level)}
              disabled={!availability.levels}
              onChange={() => toggleLevel(level)}
            >
              {text.searchLevelLabels[level]}
            </Checkbox>
          ))}
        </div>
      </section>

      <section className="ov-file-browser-search__group">
        <h4 className="ov-file-browser-search__group-title">
          {text.searchTime}
          {!availability.time ? (
            <span className="ov-file-browser-search__group-note">
              {text.searchFilterUnsupported}
            </span>
          ) : null}
        </h4>
        <div className="ov-file-browser-search__row">
          <Radio
            name={timeFieldName}
            checked={filters.timeField === 'created_at'}
            disabled={!availability.time}
            onChange={() => onChange({ ...filters, timeField: 'created_at' })}
          >
            {text.searchCreatedAt}
          </Radio>
          <Radio
            name={timeFieldName}
            checked={filters.timeField === 'updated_at'}
            disabled={!availability.time}
            onChange={() => onChange({ ...filters, timeField: 'updated_at' })}
          >
            {text.searchUpdatedAt}
          </Radio>
        </div>
        {availability.time && filters.timeField ? (
          <div className="ov-file-browser-search__dates">
            <DatePicker
              aria-label={text.searchSince}
              placeholder={text.searchSince}
              value={filters.since}
              locale={locale}
              onChange={(next) => onChange({ ...filters, since: next })}
            />
            <span aria-hidden="true">–</span>
            <DatePicker
              aria-label={text.searchUntil}
              placeholder={text.searchUntil}
              value={filters.until}
              locale={locale}
              onChange={(next) => onChange({ ...filters, until: next })}
            />
          </div>
        ) : null}
      </section>
    </>
  );
}
