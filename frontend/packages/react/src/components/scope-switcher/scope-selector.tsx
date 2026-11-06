import { useCallback, useEffect, useId, useRef, useState } from 'react';
import chevronDown from '../../../../icons/svg/scope-switcher/chevron-down.svg?inline';
import { ScopePanel } from './scope-panel.js';
import type { ScopeSelectorProps } from './types.js';

export function ScopeSelector({ level, labels }: ScopeSelectorProps) {
  const [localVisible, setLocalVisible] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const open =
    level.kind === 'selectable' &&
    (level.visible ?? localVisible) &&
    !level.disabled;
  const onVisibleChange =
    level.kind === 'selectable' ? level.onVisibleChange : undefined;

  const changeVisible = useCallback(
    (visible: boolean) => {
      setLocalVisible(visible);
      onVisibleChange?.(visible);
    },
    [onVisibleChange],
  );

  // Synchronize document pointer/keyboard events while open. Outside clicks and
  // Escape after an async state removes the focused control do not reach React
  // handlers on this subtree. Remove both listeners on close/change/unmount.
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !root.current?.contains(event.target)
      ) {
        changeVisible(false);
      }
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        changeVisible(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', escape);
    };
  }, [open, changeVisible]);

  const content = (
    <>
      {level.icon && (
        <span className="ov-scope-switcher__icon" aria-hidden="true">
          {level.icon}
        </span>
      )}
      <span className="ov-scope-switcher__label">{level.selectedLabel}</span>
      {level.tag && <span className="ov-scope-switcher__tag">{level.tag}</span>}
    </>
  );
  return (
    <div
      ref={root}
      role="group"
      className="ov-scope-switcher__level"
      onBlur={(event) => {
        if (
          open &&
          event.relatedTarget instanceof Node &&
          !event.currentTarget.contains(event.relatedTarget)
        ) {
          changeVisible(false);
        }
      }}
    >
      {level.kind === 'fixed' ? (
        <span
          className="ov-scope-switcher__trigger"
          aria-label={level.ariaLabel}
          title={level.selectedLabel}
        >
          {content}
        </span>
      ) : (
        <button
          ref={trigger}
          type="button"
          className="ov-scope-switcher__trigger"
          aria-label={level.ariaLabel}
          title={level.selectedLabel}
          disabled={level.disabled}
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-controls={open ? panelId : undefined}
          onClick={() => changeVisible(!open)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              if (!open) changeVisible(true);
              else
                root.current
                  ?.querySelector<HTMLElement>(
                    '[role="option"]:not([aria-disabled="true"])',
                  )
                  ?.focus();
            }
          }}
        >
          {content}
          <span className="ov-scope-switcher__arrow" aria-hidden="true">
            <img src={chevronDown} alt="" width={14} height={14} />
          </span>
        </button>
      )}
      {open && level.kind === 'selectable' && (
        <div
          id={panelId}
          className="ov-scope-switcher__panel"
          role="dialog"
          aria-label={level.ariaLabel ?? level.selectedLabel}
        >
          <ScopePanel
            level={{
              ...level,
              onSelect: (option) => {
                changeVisible(false);
                level.onSelect?.(option);
              },
            }}
            labels={labels}
          />
        </div>
      )}
    </div>
  );
}
