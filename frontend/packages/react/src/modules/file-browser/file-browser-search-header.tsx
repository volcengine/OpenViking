import type { ComponentPropsWithoutRef, RefObject } from 'react';

import FilterIcon from '../../../../icons/svg/file-browser/search-filter.svg?react';
import { FileBrowserSearchModeMenu } from './file-browser-search-mode-menu.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 弹窗头部包含搜索输入、检索方式和高级筛选开关。
 *
 * 关闭按钮由 `Modal` 自己渲染，这里不再画一个——两个关闭按钮位置不同、尺寸
 * 不同，会让人不知道该点哪个。筛选按钮放在它左侧。
 *
 * `Modal` 提供关闭按钮和弹窗的无障碍名称。
 *
 * The dialog header contains the search input, retrieval mode and advanced-filter toggle.
 *
 * The close button comes from `Modal`, so none is drawn here — two close buttons in
 * different places and sizes leave the user unsure which to press. The filter button
 * sits to its left.
 *
 * `Modal` provides the dialog’s accessible name.
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
  onSelectMode: (mode: SearchMode) => void;
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
  onSelectMode,
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
      <FileBrowserSearchModeMenu
        value={mode}
        text={text}
        onSelect={onSelectMode}
      />
      <button
        type="button"
        className="ov-file-browser-search__advanced-toggle"
        aria-label={text.searchAdvancedLabel}
        aria-expanded={advancedOpen}
        onClick={onToggleAdvanced}
      >
        <FilterIcon width={28} height={28} aria-hidden="true" />
      </button>
    </div>
  );
}
