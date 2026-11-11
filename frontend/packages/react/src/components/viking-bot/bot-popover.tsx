import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useOverlayPosition } from '../../shared/components/overlay/use-overlay-position.js';
import { navigatePopoverTab } from './popover-tab-navigation.js';

const subscribe = () => () => {};

/** Private non-modal popover shared by history and the reference toolbar. */
export function BotPopover({
  label,
  trigger: renderTrigger,
  children,
  placement = 'bottom',
  onOpen,
  hover = false,
}: {
  label: string;
  trigger: (props: ButtonHTMLAttributes<HTMLButtonElement>) => ReactNode;
  children: (close: () => void) => ReactNode;
  placement?: 'top' | 'bottom';
  onOpen?: () => void;
  hover?: boolean;
}) {
  const mounted = useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
  const native = mounted && 'popover' in HTMLElement.prototype;
  const [open, setOpen] = useState(false);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const anchor = useRef<HTMLSpanElement>(null);
  const dismissTimer = useRef<number | undefined>(undefined);
  const skipFocus = useRef(false);
  const id = useId();
  const position = useOverlayPosition(open, anchor, surface, placement, 'end');
  const cancelDismiss = () => window.clearTimeout(dismissTimer.current);
  const hide = () => {
    cancelDismiss();
    if (native) surface?.hidePopover();
    else setOpen(false);
  };
  const show = () => {
    cancelDismiss();
    if (native)
      surface?.showPopover({
        source: anchor.current?.querySelector('button') ?? undefined,
      });
    else {
      setOpen(true);
      if (!open) onOpen?.();
    }
  };
  const close = () => {
    skipFocus.current = true;
    hide();
    anchor.current?.querySelector('button')?.focus();
    skipFocus.current = false;
  };
  const interest: HTMLAttributes<HTMLElement> = hover
    ? {
        onMouseEnter: show,
        onMouseLeave: () => {
          cancelDismiss();
          dismissTimer.current = window.setTimeout(() => {
            if (
              !anchor.current?.contains(document.activeElement) &&
              !surface?.contains(document.activeElement)
            )
              hide();
          }, 100);
        },
        onFocus: () => {
          if (!skipFocus.current) show();
        },
        onBlur: (event) => {
          const next = event.relatedTarget as Node | null;
          if (
            next &&
            !anchor.current?.contains(next) &&
            !surface?.contains(next)
          )
            hide();
        },
        onKeyDown: (event) => {
          if (open && !native)
            navigatePopoverTab(event, anchor.current, surface, close);
          if (open && event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
        },
      }
    : {};
  // Effect necessity: hover-leave handlers schedule a browser timer; release it
  // on unmount so a detached popup cannot receive a late close. Event handlers
  // replace/cancel the timer during pointer and focus transitions.
  useEffect(() => () => window.clearTimeout(dismissTimer.current), []);
  // Effect necessity: native popover toggles, including light dismiss/Escape, are
  // browser events outside React. Subscribe after the portal DOM commits and
  // remove the listener when its node or callback changes, or on unmount.
  useEffect(() => {
    if (!surface) return;
    const toggle = () => {
      const expanded = surface.matches(':popover-open');
      setOpen(expanded);
      if (expanded) onOpen?.();
    };
    if (native) surface.addEventListener('toggle', toggle);
    return () => surface.removeEventListener('toggle', toggle);
  }, [native, surface, onOpen]);
  // Effect necessity: focus the newly committed popup DOM; the opening event runs
  // before React mounts its children. This one-time focus owns no subscription or
  // timer, so closing/unmounting requires no cleanup or focus restoration here.
  useEffect(() => {
    if (!open || hover) return;
    (surface?.querySelector<HTMLElement>('input, button') ?? surface)?.focus();
  }, [open, surface, hover]);
  // Effect necessity: the fallback synchronizes with global pointer/key events
  // while open, including events outside this subtree. Register after commit;
  // remove both document listeners on close, node replacement or unmount.
  useEffect(() => {
    if (native || !open) return;
    const outside = (event: MouseEvent) => {
      const node = event.target as Node;
      if (!anchor.current?.contains(node) && !surface?.contains(node))
        setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      anchor.current?.querySelector('button')?.focus();
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [native, open, surface]);
  const invoker = native ? { popovertarget: id } : {};
  return (
    <span className="ov-viking-bot__popover-anchor" ref={anchor} {...interest}>
      {renderTrigger({
        ...invoker,
        'aria-haspopup': 'dialog',
        'aria-controls': id,
        'aria-expanded': open,
        onClick: hover
          ? (event) => {
              event.preventDefault();
              show();
            }
          : native
            ? undefined
            : () => {
                setOpen(!open);
                if (!open) onOpen?.();
              },
      })}
      {mounted
        ? createPortal(
            <div
              ref={setSurface}
              id={id}
              popover={native ? 'auto' : undefined}
              role="dialog"
              tabIndex={-1}
              aria-label={label}
              hidden={!native && !open}
              className={`ov-viking-bot__popover ov-viking-bot__popover--${placement}`}
              style={{ top: position.top, left: position.left }}
              {...interest}
            >
              {open ? children(close) : null}
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
