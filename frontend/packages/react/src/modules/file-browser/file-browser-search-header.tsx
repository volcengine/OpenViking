import type { ComponentPropsWithoutRef, RefObject } from 'react';

import FilterIcon from '../../../../icons/svg/dashboards/severity-filter.svg?react';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 弹窗头部：一个无边框的搜索输入，右侧是高级筛选开关。
 *
 * 关闭按钮由 `Modal` 自己渲染，这里不再画一个——两个关闭按钮位置不同、尺寸
 * 不同，会让人不知道该点哪个。漏斗放在它左侧，与设计稿一致。
 *
 * 没有可见标题：设计稿的头部只有输入框和图标。`Modal` 仍收到 `title` 作无障碍名。
 *
 * The dialog header: a borderless search input with the advanced-filter toggle on the
 * right.
 *
 * The close button comes from `Modal`, so none is drawn here — two close buttons in
 * different places and sizes leave the user unsure which to press. The funnel sits to
 * its left, matching the design.
 *
 * There is no visible title: the design's header holds only the input and the icons.
 * `Modal` still receives `title` as its accessible name.
 */
export interface FileBrowserSearchHeaderProps {
  mode: SearchMode;
  draft: string;
  text: ResolvedLabels;
  inputRef: RefObject<HTMLInputElement | null>;
  advancedOpen: boolean;
  inputProps: Pick<
    ComponentPropsWithoutRef<'input'>,
    | 'onKeyDown'
    | 'onCompositionStart'
    | 'onCompositionEnd'
    | 'role'
    | 'aria-controls'
    | 'aria-expanded'
    | 'aria-activedescendant'
    | 'aria-autocomplete'
  >;
  onChange: (value: string) => void;
  onToggleAdvanced: () => void;
}

export function FileBrowserSearchHeader({
  mode,
  draft,
  text,
  inputRef,
  advancedOpen,
  inputProps,
  onChange,
  onToggleAdvanced,
}: FileBrowserSearchHeaderProps) {
  return (
    <div className="ov-file-browser-search__header">
      <input
        {...inputProps}
        ref={inputRef}
        type="text"
        className="ov-file-browser-search__input"
        aria-label={text.searchTitle}
        placeholder={text.searchModePlaceholders[mode]}
        value={draft}
        onChange={(event) => onChange(event.target.value)}
      />
      <button
        type="button"
        className="ov-file-browser-search__advanced-toggle"
        aria-label={text.searchAdvancedLabel}
        aria-expanded={advancedOpen}
        onClick={onToggleAdvanced}
      >
        <FilterIcon width={16} height={16} aria-hidden="true" />
      </button>
    </div>
  );
}
