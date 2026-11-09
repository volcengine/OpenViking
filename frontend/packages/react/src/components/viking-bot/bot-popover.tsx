import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useTooltipPosition } from '../../shared/components/tooltip/use-tooltip-position.js';

const subscribe = () => () => {};

/** Private non-modal popover shared by history and the reference toolbar. */
export function BotPopover({
  label,
  trigger: renderTrigger,
  children,
  placement = 'bottom',
  onOpen,
}: {
  label: string;
  trigger: (props: ButtonHTMLAttributes<HTMLButtonElement>) => ReactNode;
  children: (close: () => void) => ReactNode;
  placement?: 'top' | 'bottom';
  onOpen?: () => void;
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
  const id = useId();
  const position = useTooltipPosition(open, anchor, surface, placement, 'end');
  const close = () => {
    if (native) surface?.hidePopover();
    else setOpen(false);
    anchor.current?.querySelector('button')?.focus();
  };
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
    if (!open) return;
    (surface?.querySelector<HTMLElement>('input, button') ?? surface)?.focus();
  }, [open, surface]);
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
    <span className="ov-viking-bot__popover-anchor" ref={anchor}>
      {renderTrigger({
        ...invoker,
        'aria-haspopup': 'dialog',
        'aria-controls': id,
        'aria-expanded': open,
        onClick: native
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
              popover="auto"
              role="dialog"
              tabIndex={-1}
              aria-label={label}
              hidden={!native && !open}
              className={`ov-viking-bot__popover ov-viking-bot__popover--${placement}`}
              style={{ top: position.top, left: position.left }}
            >
              {open ? children(close) : null}
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
