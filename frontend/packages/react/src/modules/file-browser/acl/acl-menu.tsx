import { useEffect, useId, useRef, useState } from 'react';
import Chevron from '../../../../../icons/svg/scope-switcher/chevron-down.svg?react';
import Check from '../../../../../icons/svg/scope-switcher/check.svg?react';
import type { FileBrowserAclLevel } from '../acl-types.js';
import { LEVELS } from './acl-model.js';
import { levelLabel, type AclText } from './acl-labels.js';

export function AclMenu({
  level,
  text,
  name,
  disabled,
  onChange,
  onRemove,
}: {
  level: FileBrowserAclLevel;
  text: AclText;
  name: string;
  disabled?: boolean;
  onChange: (level: FileBrowserAclLevel) => void;
  onRemove?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  // Menu focus and outside-pointer detection require the committed DOM. Move
  // focus only after its buttons exist; remove the document listener on close
  // and unmount so a closed menu no longer handles unrelated pointer events.
  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target))
        setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  return (
    <div
      className="ov-file-browser-acl__menu-anchor"
      ref={root}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="ov-file-browser-acl__level"
        aria-label={`${name}: ${levelLabel(level, text)}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        disabled={disabled}
        onClick={() => setOpen(!open)}
      >
        {levelLabel(level, text)}
        <Chevron aria-hidden="true" width={12} height={12} />
      </button>
      {open && !disabled ? (
        <div
          id={id}
          role="menu"
          tabIndex={-1}
          aria-label={name}
          ref={menu}
          className="ov-file-browser-acl__menu"
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.preventDefault();
              event.stopPropagation();
              close();
            }
            const buttons = [...event.currentTarget.querySelectorAll('button')];
            const index = buttons.indexOf(
              document.activeElement as HTMLButtonElement,
            );
            const last = buttons.length - 1;
            const next =
              event.key === 'ArrowDown'
                ? (index + 1) % buttons.length
                : event.key === 'ArrowUp'
                  ? (index + last) % buttons.length
                  : event.key === 'Home'
                    ? 0
                    : event.key === 'End'
                      ? last
                      : -1;
            if (next >= 0) {
              event.preventDefault();
              buttons[next]?.focus();
            }
          }}
        >
          {LEVELS.map((value) => (
            <button
              key={value}
              type="button"
              role="menuitemradio"
              aria-checked={level === value}
              onClick={() => {
                close();
                if (value !== level) onChange(value);
              }}
            >
              <span className="ov-file-browser-acl__check">
                {level === value ? (
                  <Check width={12} height={12} aria-hidden="true" />
                ) : null}
              </span>
              {levelLabel(value, text)}
            </button>
          ))}
          {onRemove ? (
            <button
              type="button"
              role="menuitem"
              className="ov-file-browser-acl__remove"
              onClick={() => {
                close();
                onRemove();
              }}
            >
              {text.remove}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
