import Chevron from '../../../../../icons/svg/scope-switcher/chevron-down.svg?react';
import { DropdownSurface } from '../../../shared/primitives/dropdown/dropdown-surface.js';
import { Input } from '../../../shared/primitives/input/input.js';
import { SearchIcon } from '../file-browser-icons.js';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { OvIdentityValue } from '../../../client/types.js';
import type {
  FileBrowserAclContext,
  FileBrowserAclEntry,
  FileBrowserAclLevel,
  FileBrowserAclOptions,
} from '../acl-types.js';
import { AclPrincipalPicker } from './acl-principal-picker.js';
import { filterCandidates } from './acl-principal-model.js';
import type { AclText } from './acl-labels.js';
import { useAclPrincipals } from './use-acl-principals.js';

const noop = () => undefined;
const allTypes = ['user', 'group'] as const;

function focusAfterLoad(node: HTMLInputElement | null) {
  const modal = node?.closest('.ov-modal');
  // The permission report can load after the focus lock is already active.
  // Move focus only when it is still on the dialog's initial Close button.
  if (
    modal &&
    modal.querySelector('.ov-modal__close') ===
      node?.ownerDocument.activeElement
  )
    node?.focus();
}

export function AclAddPrincipal({
  entries,
  administratorId,
  text,
  disabled,
  context,
  accountId,
  options,
  onAdd,
}: {
  entries: FileBrowserAclEntry[];
  administratorId?: string;
  text: AclText;
  disabled: boolean;
  context: FileBrowserAclContext;
  accountId?: OvIdentityValue;
  options?: FileBrowserAclOptions;
  onAdd: (entries: FileBrowserAclEntry[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState('');
  const supported = options?.supportedPrincipalTypes ?? allTypes;
  const [preferredType, setType] = useState<'user' | 'group'>('user');
  const type = supported.includes(preferredType)
    ? preferredType
    : (supported[0] ?? 'user');
  const [pending, setPending] = useState<Record<string, FileBrowserAclLevel>>(
    {},
  );
  const id = useId();
  const anchor = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const attachInput = useCallback((node: HTMLInputElement | null) => {
    input.current = node;
    focusAfterLoad(node);
  }, []);
  const insidePointer = useRef<PointerEvent>(null);
  const directory = useAclPrincipals({
    open: open && !disabled && supported.length > 0,
    type,
    keyword,
    context,
    accountId,
    options,
  });
  const existing = new Set(entries.map((entry) => entry.principal));
  if (administratorId) existing.add(`user:${administratorId}`);
  const candidates = filterCandidates(
    type,
    keyword,
    directory.items,
    existing,
    text,
  );
  const reset = useCallback(() => {
    setOpen(false);
    setKeyword('');
    setPending({});
  }, []);
  // The portaled picker and its permission menus share React event ancestry,
  // although their DOM nodes sit outside the modal. Observe outside presses on
  // the document until the picker closes, then remove the listener.
  useEffect(() => {
    if (!open || disabled) return;
    const outside = (event: PointerEvent) => {
      if (insidePointer.current !== event) reset();
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open, disabled, reset]);
  const selected = Object.entries(pending).filter(
    ([principal]) =>
      !existing.has(principal) &&
      supported.some((value) => principal.startsWith(`${value}:`)),
  );
  const close = () => {
    reset();
    input.current?.focus();
  };
  if (!supported.length) return null;
  return (
    <div
      ref={anchor}
      role="presentation"
      className="ov-file-browser-acl__add"
      onPointerDownCapture={(event) => {
        insidePointer.current = event.nativeEvent;
        if (
          event.target instanceof Node &&
          anchor.current?.contains(event.target)
        ) {
          setOpen(true);
        }
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || !open || event.defaultPrevented) return;
        event.preventDefault();
        event.stopPropagation();
        close();
      }}
    >
      <Input
        ref={attachInput}
        data-autofocus
        prefix={<SearchIcon />}
        suffix={<Chevron aria-hidden="true" width={12} height={12} />}
        id={id}
        type="search"
        role="combobox"
        aria-label={text.addPrincipal}
        aria-haspopup="dialog"
        value={keyword}
        placeholder={open ? text.search : text.addPrincipal}
        disabled={disabled}
        aria-expanded={open && !disabled}
        aria-controls={open ? `${id}-options` : undefined}
        onClick={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowDown' && event.key !== 'Enter') return;
          event.preventDefault();
          setOpen(true);
        }}
        onChange={(event) => {
          setKeyword(event.target.value);
          setOpen(true);
        }}
      />
      {open && !disabled ? (
        <DropdownSurface
          anchor={anchor}
          className="ov-file-browser-acl-picker"
          alignment="start"
          matchAnchorWidth
          onReady={noop}
        >
          <AclPrincipalPicker
            id={`${id}-options`}
            directory={directory}
            supported={supported}
            type={type}
            candidates={candidates}
            pending={pending}
            hasKeyword={Boolean(keyword.trim())}
            canAdd={selected.length > 0}
            text={text}
            onTypeChange={setType}
            onLevelChange={(principal, level) =>
              setPending((previous) => {
                const next = { ...previous };
                if (level) next[principal] = level;
                else delete next[principal];
                return next;
              })
            }
            onCancel={close}
            onAdd={() => {
              onAdd(
                selected.map(([principal, level]) => ({ principal, level })),
              );
              close();
            }}
          />
        </DropdownSurface>
      ) : null}
    </div>
  );
}
