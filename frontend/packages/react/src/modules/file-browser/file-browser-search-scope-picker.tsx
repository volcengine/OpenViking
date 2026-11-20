import { useId, useRef, useState } from 'react';
import FolderIcon from '../../../../icons/svg/file-browser/search-folder-open.svg?react';
import ChevronIcon from '../../../../icons/svg/file-browser/search-chevron-down.svg?react';
import type { OvRequestOptions } from '../../client/types.js';
import { useOvClient } from '../../client/provider.js';
import { scopeKeyOf } from '../../shared/request/fs-scope.js';
import { Popover } from '../../shared/primitives/popover/popover.js';
import { formatVikingUriLabel } from '../../shared/path-label.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { FileBrowserSearchScopeDraft } from './file-browser-search-scope-draft.js';
import type { ScopeOption } from './search-types.js';

interface FileBrowserSearchScopePickerProps {
  scopes: readonly ScopeOption[];
  value: readonly string[];
  multiple: boolean;
  text: ResolvedLabels;
  requestOptions?: OvRequestOptions;
  onChange: (value: string[]) => void;
}

export function FileBrowserSearchScopePicker(
  props: FileBrowserSearchScopePickerProps,
) {
  const owner = scopeKeyOf(useOvClient('local'), props.requestOptions);
  return (
    <ScopePicker
      key={`${owner}:${props.multiple}:${props.scopes.map(({ uri }) => uri).join('\n')}`}
      {...props}
    />
  );
}

function ScopePicker({
  scopes,
  value,
  multiple,
  text,
  requestOptions,
  onChange,
}: FileBrowserSearchScopePickerProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const selected = value.filter(
    (item) => !scopes.some((scope) => scope.uri === item),
  );

  function close() {
    setOpen(false);
    button.current?.focus();
  }

  return (
    <div className="ov-file-browser-search__scope-picker">
      <Popover
        trigger="click"
        placement="bottom"
        alignment="start"
        open={open}
        onOpenChange={setOpen}
        className="ov-file-browser-search__scope-popover"
        content={
          open ? (
            <FileBrowserSearchScopeDraft
              key={value.join('\n')}
              id={id}
              scopes={scopes}
              value={value}
              multiple={multiple}
              text={text}
              requestOptions={requestOptions}
              onCancel={close}
              onConfirm={(next) => {
                onChange(next);
                close();
              }}
            />
          ) : null
        }
      >
        <button
          ref={button}
          type="button"
          className="ov-file-browser-search__scope-trigger"
          aria-expanded={open}
          aria-controls={open ? id : undefined}
        >
          <FolderIcon aria-hidden="true" />
          <span className="ov-file-browser-search__scope-trigger-label">
            {text.searchPickScope}
          </span>
          <ChevronIcon aria-hidden="true" />
        </button>
      </Popover>
      {selected.length > 0 ? (
        <ul className="ov-file-browser-search__options">
          {selected.map((item) => (
            <li key={item}>
              <button
                type="button"
                className="ov-file-browser-search__scope-choice"
                aria-pressed="true"
                title={formatVikingUriLabel(item)}
                onClick={() => onChange(value.filter((uri) => uri !== item))}
              >
                <span>{formatVikingUriLabel(item)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
