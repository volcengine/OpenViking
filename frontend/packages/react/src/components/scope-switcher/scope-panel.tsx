import { useCallback } from 'react';
import { moveScopeFocus } from './scope-keyboard.js';
import check from '../../../../icons/svg/scope-switcher/check.svg?inline';
import { SCOPE_SWITCHER_SEARCH_THRESHOLD } from './types.js';
import type {
  ScopeSwitcherLabels,
  ScopeSwitcherSelectableLevel,
} from './types.js';

function Highlight({ text, keyword }: { text: string; keyword: string }) {
  const index = text.toLocaleLowerCase().indexOf(keyword.toLocaleLowerCase());
  if (!keyword || index < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + keyword.length)}</mark>
      {text.slice(index + keyword.length)}
    </>
  );
}

export function ScopePanel({
  level,
  labels,
}: {
  level: ScopeSwitcherSelectableLevel;
  labels?: ScopeSwitcherLabels;
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
  const keyword = level.searchKeyword ?? '';
  const searchVisible =
    level.onSearchKeywordChange &&
    (keyword.length > 0 ||
      Math.max(level.options.length, level.total ?? 0) >
        SCOPE_SWITCHER_SEARCH_THRESHOLD);
  const search = searchVisible ? (
    <input
      type="search"
      className="ov-scope-switcher__search"
      value={keyword}
      aria-label={labels?.searchPlaceholder ?? 'Search'}
      placeholder={labels?.searchPlaceholder ?? 'Search'}
      onChange={(event) =>
        level.onSearchKeywordChange?.(event.currentTarget.value)
      }
    />
  ) : null;
  let list;
  if (level.loading) {
    list = (
      <div className="ov-scope-switcher__state" role="status">
        {labels?.loading ?? 'Loading…'}
      </div>
    );
  } else if (level.error) {
    list = (
      <div className="ov-scope-switcher__state" role="alert">
        {level.errorFallback ?? labels?.error ?? 'Unable to load options'}
      </div>
    );
  } else if (!level.options.length) {
    list = (
      <div className="ov-scope-switcher__state" role="status">
        {level.emptyFallback ??
          (keyword
            ? (labels?.noMatch ?? labels?.empty ?? 'No matches')
            : (labels?.empty ?? 'No options'))}
      </div>
    );
  } else {
    list = (
      <div
        key={keyword}
        className="ov-scope-switcher__list"
        role="listbox"
        aria-label={level.ariaLabel ?? level.selectedLabel}
        aria-busy={level.busy}
        onScroll={(event) => {
          const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
          level.onScroll?.({ scrollTop, scrollHeight, clientHeight });
        }}
      >
        {level.options.map((option) => {
          const selected = option.id === level.selectedId;
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
                  <img src={check} alt="" width={16} height={16} />
                </span>
              )}
            </>
          );
          return (
            <div
              key={option.id}
              tabIndex={option.disabled || level.busy ? -1 : 0}
              role="option"
              aria-selected={selected}
              className="ov-scope-switcher__option"
              title={option.label}
              aria-disabled={option.disabled || level.busy || undefined}
              onClick={(event) => {
                const target = event.target as HTMLElement;
                if (
                  option.disabled ||
                  level.busy ||
                  target.closest(
                    'button, a, input, select, textarea, [role="button"]',
                  )
                )
                  return;
                level.onSelect?.(option);
              }}
              onKeyDown={(event) => {
                if (
                  event.target !== event.currentTarget ||
                  option.disabled ||
                  level.busy
                )
                  return;
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  level.onSelect?.(option);
                }
              }}
            >
              {level.renderOption
                ? level.renderOption(content, option)
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
      onKeyDownCapture={moveScopeFocus}
    >
      {level.renderPanel ? (
        level.renderPanel({ search, list })
      ) : (
        <>
          {search}
          {list}
        </>
      )}
    </div>
  );
}
