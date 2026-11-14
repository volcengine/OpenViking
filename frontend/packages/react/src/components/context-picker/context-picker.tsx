import { useRef } from 'react';
import { useLocale } from '../../shared/locale-context.js';
import { resolveLocale } from '../../shared/locale.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import { Button } from '../../shared/primitives/button/button.js';
import { FolderIcon, DocumentIcon } from '../../icons/index.js';
import { PickerTree } from './picker-tree.js';
import type { FileTreeHandle, FileTreeEntryRow } from '../file-tree/types.js';
import { normalizeUri, useContextReveal } from './use-context-reveal.js';
import type { ContextPickerProps } from './types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

function Picker(props: ContextPickerProps) {
  const {
    rows,
    value,
    onChange,
    onCancel,
    onConfirm,
    multiple = false,
    confirming = false,
    disabled = false,
    slots,
  } = props;
  const locale = useLocale(props.locale);
  const defaults = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
  const text = {
    ...defaults,
    ...Object.fromEntries(
      Object.entries(props.labels ?? {}).filter(
        ([, value]) => value !== undefined,
      ),
    ),
  };
  const anchor = useRef<HTMLElement>(null);
  const tree = useRef<FileTreeHandle>(null);
  const stopReveal = useContextReveal(props, tree);
  const blocked = disabled || confirming;
  const selection = multiple ? value : value.slice(0, 1);
  const selected = new Set(selection.map((entry) => normalizeUri(entry.uri)));
  const select = (row: FileTreeEntryRow) => {
    if (blocked || row.disabled || row.selectable === false) return;
    stopReveal();
    onChange?.(
      multiple
        ? selected.has(normalizeUri(row.uri))
          ? selection.filter(
              (entry) => normalizeUri(entry.uri) !== normalizeUri(row.uri),
            )
          : [...selection, row]
        : [row],
    );
  };
  const invalid = rows.some(
    (row) =>
      row.kind === 'entry' &&
      selected.has(normalizeUri(row.uri)) &&
      (row.disabled || row.selectable === false),
  );
  const confirmButton = (
    <Button
      type="primary"
      loading={confirming}
      disabled={
        blocked ||
        props.isReady === false ||
        props.loading ||
        !onConfirm ||
        props.confirmDisabled ||
        !selection.length ||
        invalid
      }
      onClick={() => onConfirm?.(selection)}
    >
      {text.confirm}
    </Button>
  );
  return (
    <Modal
      open
      title={props.title ?? text.title}
      locale={locale}
      triggerRef={props.triggerRef ?? anchor}
      onClose={blocked ? undefined : onCancel}
      className={[
        'ov-context-picker',
        multiple && 'ov-context-picker--multiple',
        props.className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <h2 className="ov-context-picker__title">{props.title ?? text.title}</h2>
      <div
        className="ov-context-picker__content"
        aria-busy={props.loading || confirming || undefined}
      >
        {(props.showSelection ?? !multiple) ? (
          slots?.selection !== undefined ? (
            slots.selection
          ) : (
            <div className="ov-context-picker__selection">
              <span>{text.selected}</span>
              {!selection.length ? <span>{text.unselected}</span> : null}
              {selection.map((entry) => (
                <span
                  key={entry.uri}
                  className="ov-context-picker__selected-entry"
                >
                  {entry.type === 'directory' ? (
                    <FolderIcon width={14} height={14} />
                  ) : (
                    <DocumentIcon width={14} height={14} />
                  )}
                  {props.onOpenEntry ? (
                    <Button
                      type="text"
                      disabled={blocked}
                      onClick={() => props.onOpenEntry?.(entry)}
                    >
                      {entry.uri}
                    </Button>
                  ) : (
                    <span title={entry.uri}>{entry.uri}</span>
                  )}
                </span>
              ))}
            </div>
          )
        ) : null}
        <PickerTree
          props={props}
          locale={locale}
          selected={selected}
          select={select}
          blocked={blocked}
          stopReveal={stopReveal}
          tree={tree}
          searchLabel={text.search}
          emptyLabel={text.empty}
          clearLabel={text.clearSearch}
        />
        {slots?.content}
        {props.error ? (
          <div className="ov-context-picker__error" role="alert">
            {props.error}
          </div>
        ) : null}
      </div>
      <div className="ov-context-picker__footer">
        <div className="ov-context-picker__footer-start">
          {slots?.footerStart !== undefined
            ? slots.footerStart
            : multiple
              ? text.selectedCount(selection.length)
              : null}
        </div>
        <div className="ov-context-picker__actions">
          <Button disabled={blocked} onClick={onCancel}>
            {text.cancel}
          </Button>
          {slots?.confirm ? slots.confirm(confirmButton) : confirmButton}
        </div>
      </div>
    </Modal>
  );
}

/** Closing unmounts presentation state; the host retains all selection and query data. */
export function ContextPicker(props: ContextPickerProps) {
  return props.open ? <Picker {...props} /> : null;
}
