import { useCallback, type ReactNode } from 'react';
import { moveScopeFocus } from './scope-keyboard.js';
import CheckIcon from '../../../../icons/svg/scope-switcher/check.svg?react';
import SearchIcon from '../../../../icons/svg/scope-switcher/search.svg?react';
import { SCOPE_SWITCHER_SEARCH_THRESHOLD } from './types.js';
import type {
  ScopeSwitcherActions,
  ScopeSwitcherData,
  ScopeSwitcherLabels,
  ScopeSwitcherSlots,
  ScopeSwitcherState,
} from './types.js';

/**
 * 把标签里所有命中关键字的片段包进 `<mark>`，逐处高亮而不是只标第一处。大小写不敏感，保留原文大小写。
 *
 * Wraps every keyword hit in the label with `<mark>`, highlighting each occurrence
 * rather than only the first. Case-insensitive; the original casing is preserved.
 */
function Highlight({ text, keyword }: { text: string; keyword: string }) {
  const needle = keyword.trim().toLocaleLowerCase();
  if (!needle) return <>{text}</>;
  const haystack = text.toLocaleLowerCase();
  const parts: ReactNode[] = [];
  let cursor = 0;
  let hit = haystack.indexOf(needle);
  while (hit >= 0) {
    if (hit > cursor) parts.push(text.slice(cursor, hit));
    const end = hit + needle.length;
    parts.push(<mark key={`${hit}-${end}`}>{text.slice(hit, end)}</mark>);
    cursor = end;
    hit = haystack.indexOf(needle, cursor);
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

export function ScopePanel({
  label,
  data,
  state,
  slots,
  actions,
  onSelect,
  labels,
}: {
  /** Accessible name of the list and search box. */
  label: string;
  data: ScopeSwitcherData;
  state?: ScopeSwitcherState;
  slots?: ScopeSwitcherSlots;
  actions?: ScopeSwitcherActions;
  onSelect: NonNullable<ScopeSwitcherActions['onSelect']>;
  labels: Required<ScopeSwitcherLabels>;
}) {
  const focusPanel = useCallback((node: HTMLDivElement | null) => {
    const target =
      node?.querySelector<HTMLElement>('input') ??
      node?.querySelector<HTMLElement>(
        '[role="option"][aria-selected="true"]:not([aria-disabled="true"])',
      ) ??
      node?.querySelector<HTMLElement>(
        '[role="option"]:not([aria-disabled="true"])',
      );
    (target ?? node)?.focus();
  }, []);
  // Render the visible subset; fall back to the authoritative set when the host
  // does not filter. Validity of the selection is decided by the caller from
  // `data.options`, so filtering here never changes the scope.
  const options = data.visibleOptions ?? data.options;
  const keyword = state?.searchKeyword ?? '';
  const hasKeyword = keyword.trim().length > 0;
  // Base the threshold on the authoritative set, not the rendered subset: a host
  // that pages or filters passes a handful of `visibleOptions` while the whole
  // set is well past the threshold, and searching is exactly what it needs.
  const searchVisible =
    actions?.onSearchKeywordChange &&
    (state?.alwaysShowSearch ||
      hasKeyword ||
      Math.max(options.length, data.options.length, data.total ?? 0) >
        SCOPE_SWITCHER_SEARCH_THRESHOLD);
  const search = searchVisible ? (
    <div className="ov-scope-switcher__search-field">
      <SearchIcon className="ov-scope-switcher__search-icon" />
      <input
        type="search"
        className="ov-scope-switcher__search"
        value={keyword}
        aria-label={labels.searchPlaceholder}
        placeholder={labels.searchPlaceholder}
        onChange={(event) =>
          actions?.onSearchKeywordChange?.(event.currentTarget.value)
        }
      />
    </div>
  ) : null;
  let list;
  if (state?.loading) {
    list = (
      <div className="ov-scope-switcher__state" role="status">
        {labels.loading}
      </div>
    );
  } else if (state?.error) {
    list = (
      <div className="ov-scope-switcher__state" role="alert">
        {slots?.errorFallback ?? labels.error}
      </div>
    );
  } else if (!options.length) {
    list = (
      <div className="ov-scope-switcher__state" role="status">
        {slots?.emptyFallback ?? (hasKeyword ? labels.noMatch : labels.empty)}
      </div>
    );
  } else {
    list = (
      <div
        key={keyword}
        className="ov-scope-switcher__list"
        role="listbox"
        aria-label={label}
        aria-busy={state?.busy}
        onScroll={(event) => {
          const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
          actions?.onScroll?.({ scrollTop, scrollHeight, clientHeight });
        }}
      >
        {options.map((option) => {
          const selected = option.id === data.selectedId;
          const content = (
            <>
              {option.icon && (
                <span className="ov-scope-switcher__icon" aria-hidden="true">
                  {option.icon}
                </span>
              )}
              <span className="ov-scope-switcher__option-text">
                <span>
                  <Highlight text={option.label} keyword={keyword} />
                </span>
                {option.description && <small>{option.description}</small>}
              </span>
              {selected && (
                <span className="ov-scope-switcher__check" aria-hidden="true">
                  <CheckIcon />
                </span>
              )}
            </>
          );
          return (
            <div
              key={option.id}
              tabIndex={option.disabled || state?.busy ? -1 : 0}
              role="option"
              aria-selected={selected}
              className="ov-scope-switcher__option"
              title={option.label}
              aria-disabled={option.disabled || state?.busy || undefined}
              onClick={(event) => {
                const target = event.target as HTMLElement;
                if (
                  option.disabled ||
                  state?.busy ||
                  target.closest(
                    'button, a, input, select, textarea, [role="button"]',
                  )
                )
                  return;
                onSelect(option);
              }}
              onKeyDown={(event) => {
                if (
                  event.target !== event.currentTarget ||
                  option.disabled ||
                  state?.busy
                )
                  return;
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelect(option);
                }
              }}
            >
              {slots?.renderOption
                ? slots?.renderOption(content, option)
                : content}
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <div
      role="group"
      tabIndex={-1}
      ref={focusPanel}
      className="ov-scope-switcher__panel-inner"
      onKeyDownCapture={moveScopeFocus}
    >
      {slots?.renderPanel ? (
        slots?.renderPanel({ search, list })
      ) : (
        <div className="ov-scope-switcher__panel-body">
          {search}
          {list}
        </div>
      )}
    </div>
  );
}
