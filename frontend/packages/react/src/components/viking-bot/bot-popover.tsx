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
  const native =
    mounted && typeof HTMLElement.prototype.showPopover === 'function';
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
  // Native light dismiss/Escape happen outside React. Mirror the toggle event and
  // release the subscription on replacement. The fallback listens only while open.
  useEffect(() => {
    if (!surface) return;
    const toggle = () => setOpen(surface.matches(':popover-open'));
    if (native) surface.addEventListener('toggle', toggle);
    return () => surface.removeEventListener('toggle', toggle);
  }, [native, surface]);
  useEffect(() => {
    if (!open) return;
    onOpen?.();
    (surface?.querySelector<HTMLElement>('input, button') ?? surface)?.focus();
    // onOpen is an event notification; changing its identity must not reopen it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
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
        onClick: native ? undefined : () => setOpen(!open),
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
