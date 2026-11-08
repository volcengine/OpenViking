import { useCallback, useEffect, useId, useRef, useState } from 'react';
import ChevronDownIcon from '../../../../icons/svg/scope-switcher/chevron-down.svg?react';
import { ScopePanel } from './scope-panel.js';
import type { ScopeSelectorProps } from './types.js';

export function ScopeSelector(props: ScopeSelectorProps) {
  const { identity, trigger: triggerContent, labels } = props;
  const selectable = props.kind === 'selectable' ? props : undefined;
  const data = selectable?.data;
  const state = selectable?.state;
  const actions = selectable?.actions;
  const slots = selectable?.slots;

  /**
   * 回退到第一个可选项。作用域始终有一个确定的值：宿主传入的选中值缺失、为空、
   * 不在列表里或指向禁用项时，改由第一项承担，并把结果通知宿主，让 URL 与请求
   * 参数跟上。
   *
   * Falls back to the first selectable option. A scope always has a definite
   * value: when the host's selection is missing, empty, not in the list or points
   * at a disabled option, the first option takes over and the host is notified so
   * the URL and request parameters follow.
   */
  const fallback = data
    ? (data.options.find(
        (option) => option.id === data.selectedId && !option.disabled,
      ) ?? data.options.find((option) => !option.disabled))
    : undefined;
  const needsFallback =
    data !== undefined &&
    fallback !== undefined &&
    fallback.id !== data.selectedId;

  // Synchronize the host with a corrected selection. The host owns the value and
  // the URL derived from it, so a render-time fix alone would leave them stale.
  // The effect keys off the option id, not the option object: the host rebuilds
  // its list on every render, and depending on the object would re-fire while the
  // host is still writing the value back. No cleanup is needed — this is a
  // one-shot notification, not a subscription.
  const onSelect = actions?.onSelect;
  const fallbackOption = useRef<typeof fallback>(undefined);
  fallbackOption.current = needsFallback ? fallback : undefined;
  const fallbackId = needsFallback ? fallback?.id : undefined;
  useEffect(() => {
    const option = fallbackOption.current;
    if (option) onSelect?.(option);
  }, [fallbackId, onSelect]);

  const [localVisible, setLocalVisible] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const restoreSelectionFocus = useRef(false);
  const panelId = useId();
  const open =
    selectable !== undefined &&
    (state?.visible ?? localVisible) &&
    !state?.disabled;
  const onVisibleChange = actions?.onVisibleChange;

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

  // Restore DOM focus only after the panel actually closes. Controlled hosts
  // may keep it open after selection. Do not steal focus moved outside meanwhile.
  // No cleanup is needed: focus() is synchronous, the pending flag is consumed,
  // and no subscription, timer or asynchronous work survives this effect.
  // Restoring the previous focus on cleanup would undo the intended navigation.
  useEffect(() => {
    if (open || !restoreSelectionFocus.current) return;
    restoreSelectionFocus.current = false;
    if (
      document.activeElement === document.body ||
      root.current?.contains(document.activeElement)
    ) {
      trigger.current?.focus();
    }
  }, [open]);

  const selected =
    fallback ?? data?.options.find((o) => o.id === data.selectedId);
  const label = triggerContent?.label ?? selected?.label ?? '';
  const icon = triggerContent?.icon ?? selected?.icon;
  const content = (
    <>
      {icon && (
        <span className="ov-scope-switcher__icon" aria-hidden="true">
          {icon}
        </span>
      )}
      <span className="ov-scope-switcher__label">{label}</span>
      {identity.tag && (
        <span className="ov-scope-switcher__tag">{identity.tag}</span>
      )}
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
      {selectable === undefined ? (
        <span
          className="ov-scope-switcher__trigger"
          aria-label={identity.ariaLabel}
          title={label}
        >
          {content}
        </span>
      ) : (
        <button
          ref={trigger}
          type="button"
          className="ov-scope-switcher__trigger"
          aria-label={identity.ariaLabel}
          title={label}
          disabled={state?.disabled}
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
            <ChevronDownIcon />
          </span>
        </button>
      )}
      {open && selectable !== undefined && data !== undefined && (
        <div
          id={panelId}
          className={
            slots?.renderPanel
              ? 'ov-scope-switcher__panel ov-scope-switcher__panel--custom'
              : 'ov-scope-switcher__panel'
          }
          role="dialog"
          aria-label={identity.ariaLabel ?? label}
        >
          <ScopePanel
            label={identity.ariaLabel ?? label}
            data={{ ...data, selectedId: fallback?.id ?? data.selectedId }}
            state={state}
            slots={slots}
            actions={actions}
            onSelect={(option) => {
              restoreSelectionFocus.current = true;
              changeVisible(false);
              actions?.onSelect?.(option);
            }}
            labels={labels}
          />
        </div>
      )}
    </div>
  );
}
