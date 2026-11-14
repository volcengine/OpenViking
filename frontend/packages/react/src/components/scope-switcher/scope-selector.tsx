import { useTrack } from '../../shared/analytics/use-track.js';
import { useLocale } from '../../shared/locale-context.js';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import ChevronDownIcon from '../../../../icons/svg/scope-switcher/chevron-down.svg?react';
import { resolveScopeLabels } from './labels.js';
import { ScopePanel } from './scope-panel.js';
import { usePanelPosition } from './use-panel-position.js';
import type { ScopeSelectorProps } from './types.js';

export function ScopeSelector(props: ScopeSelectorProps) {
  const track = useTrack();
  const { identity, trigger: triggerContent } = props;
  const locale = useLocale(props.locale);
  const labels = resolveScopeLabels(locale, props.labels);
  const selectable = props.kind === 'selectable' ? props : undefined;
  const data = selectable?.data;
  const state = selectable?.state;
  const actions = selectable?.actions;
  const slots = selectable?.slots;

  const [localVisible, setLocalVisible] = useState(false);
  const [panel, setPanel] = useState<HTMLDivElement | null>(null);
  usePanelPosition(panel);
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

  // The component renders the host's selection as given. Normalising an invalid
  // selection belongs to whoever owns it — the host or a module such as
  // ScopeCascade — because it changes the scope, the URL and downstream requests.
  const selected = data?.options.find((o) => o.id === data.selectedId);
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
          ref={setPanel}
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
            data={data}
            state={state}
            slots={slots}
            actions={actions}
            onSelect={(option) => {
              track('scope_switcher_option_select');
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
