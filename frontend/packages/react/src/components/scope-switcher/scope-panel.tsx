import { Empty } from '../../shared/primitives/empty/empty.js';
import { Input } from '../../shared/primitives/input/input.js';
import { useCallback } from 'react';
import { Highlight } from './highlight.js';
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
    (hasKeyword ||
      Math.max(options.length, data.options.length, data.total ?? 0) >
        SCOPE_SWITCHER_SEARCH_THRESHOLD);
  const search = searchVisible ? (
    <Input
      size="small"
      prefix={<SearchIcon className="ov-scope-switcher__search-icon" />}
      type="search"
      className="ov-scope-switcher__search-field"
      value={keyword}
      aria-label={labels.searchPlaceholder}
      placeholder={labels.searchPlaceholder}
      onChange={(event) =>
        actions?.onSearchKeywordChange?.(event.currentTarget.value)
      }
    />
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
      <div className="ov-scope-switcher__state">
        {slots?.emptyFallback == null ? (
          <Empty
            size="compact"
            illustration={hasKeyword ? 'search' : 'tray'}
            title={hasKeyword ? labels.noMatch : labels.empty}
          />
        ) : (
          <div role="status">{slots.emptyFallback}</div>
        )}
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
