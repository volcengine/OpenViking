import { useCallback, useRef, type RefObject } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import CloseIcon from '../../../../icons/svg/viking-bot/remove.svg?react';
import { Input } from '../../shared/primitives/input/input.js';
import { Checkbox } from '../../shared/primitives/checkbox/checkbox.js';
import { CheckIcon } from '../../icons/index.js';
import SearchIcon from '../../../../icons/svg/scope-switcher/search.svg?react';
import { FileTree } from '../file-tree/index.js';
import type { FileTreeHandle, FileTreeEntryRow } from '../file-tree/types.js';
import type { Locale } from '../../shared/locale.js';
import { normalizeUri } from './use-context-reveal.js';
import type { ContextPickerProps } from './types.js';

export function PickerTree({
  props,
  locale,
  selected,
  select,
  blocked,
  stopReveal,
  tree,
  searchLabel,
  emptyLabel,
  clearLabel,
}: {
  props: ContextPickerProps;
  locale: Locale;
  selected: ReadonlySet<string>;
  select: (row: FileTreeEntryRow) => void;
  blocked: boolean;
  stopReveal: () => void;
  tree: RefObject<FileTreeHandle | null>;
  searchLabel: string;
  emptyLabel: string;
  clearLabel: string;
}) {
  const { rows, multiple, operations, onQueryChange, query = '' } = props;
  const input = useRef<HTMLInputElement>(null);
  const focusSearch = useCallback((node: HTMLInputElement | null) => {
    input.current = node;
    node?.focus();
  }, []);
  const selectedIds = rows
    .filter(
      (row) => row.kind === 'entry' && selected.has(normalizeUri(row.uri)),
    )
    .map((row) => row.id);
  return (
    <div className="ov-context-picker__tree-panel">
      {onQueryChange ? (
        <Input
          ref={focusSearch}
          type="search"
          prefix={<SearchIcon width={14} height={14} />}
          suffixAriaHidden={false}
          suffix={
            query ? (
              <Button
                type="text"
                aria-label={clearLabel}
                className="ov-context-picker__clear"
                disabled={blocked}
                onClick={() => {
                  stopReveal();
                  onQueryChange('');
                  input.current?.focus();
                }}
              >
                <CloseIcon width={12} height={12} aria-hidden="true" />
              </Button>
            ) : undefined
          }
          aria-label={searchLabel}
          placeholder={searchLabel}
          value={query}
          disabled={blocked}
          onChange={(event) => {
            stopReveal();
            onQueryChange(event.target.value);
          }}
        />
      ) : null}
      <FileTree
        ref={tree}
        rows={rows}
        locale={locale}
        rootOrder={props.rootOrder}
        search={false}
        refresh={false}
        loading={props.loading}
        isReady={props.isReady}
        loadingFallback={props.loadingFallback}
        errorFallback={props.errorFallback}
        emptyFallback={props.emptyFallback ?? emptyLabel}
        aria-label={props.title ?? searchLabel}
        selectedId={!multiple ? selectedIds[0] : undefined}
        selectedIds={multiple ? selectedIds : undefined}
        operations={
          blocked
            ? undefined
            : {
                ...operations,
                select: (row) => {
                  select(row);
                  return Promise.resolve();
                },
                toggleExpand: operations?.toggleExpand
                  ? (row) => {
                      stopReveal();
                      return operations.toggleExpand!(row);
                    }
                  : undefined,
                loadMore: operations?.loadMore
                  ? (row) => {
                      stopReveal();
                      return operations.loadMore!(row);
                    }
                  : undefined,
              }
        }
        getRowAttributes={(row) => ({
          'aria-label': row.kind === 'entry' ? row.name : undefined,
          'aria-disabled':
            blocked || (row.kind === 'entry' && row.disabled) || undefined,
        })}
        renderRowIcon={props.renderRowIcon}
        renderRowTooltip={props.renderRowTooltip}
        renderRowLeading={
          multiple
            ? (row) =>
                row.selectable === false ? null : (
                  <Checkbox
                    checked={selected.has(normalizeUri(row.uri))}
                    disabled={blocked || row.disabled}
                    inputTabIndex={-1}
                    className="ov-context-picker__check"
                    onClick={(event) => event.stopPropagation()}
                    onChange={() => select(row)}
                  >
                    <span className="ov-context-picker__sr">{row.name}</span>
                  </Checkbox>
                )
            : undefined
        }
        renderRowTrailing={(row) => (
          <>
            {props.renderRowTrailing?.(row)}
            {!multiple && selected.has(normalizeUri(row.uri)) ? (
              <CheckIcon
                className="ov-context-picker__checked"
                width={14}
                height={14}
              />
            ) : null}
          </>
        )}
      />
    </div>
  );
}
