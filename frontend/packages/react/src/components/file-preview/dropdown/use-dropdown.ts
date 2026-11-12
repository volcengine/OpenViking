import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';

/** Pointer hover and keyboard navigation share one controlled menu. */
export function useDropdown(hover: boolean) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  const enter = useRef<'first' | 'last' | undefined>(undefined);
  const keyboard = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const clear = () => {
    clearTimeout(timer.current);
    timer.current = undefined;
  };
  // The hover bridge uses a browser timer after pointerleave. Cancel it on unmount
  // so a queued close cannot touch a removed menu; events alone cannot clean unmounts.
  useEffect(() => () => clearTimeout(timer.current), []);
  // Browser focus may leave a pointer-open menu without blurring either of its
  // own nodes. Observe document focus while open; remove the subscription on close
  // or unmount. Local handlers cannot observe those external focus changes.
  useEffect(() => {
    if (!open) return;
    const outsideFocus = (event: FocusEvent) => {
      const target = event.target as Node;
      if (!trigger.current?.contains(target) && !menu.current?.contains(target))
        setOpen(false);
    };
    document.addEventListener('focusin', outsideFocus);
    return () => document.removeEventListener('focusin', outsideFocus);
  }, [open]);
  const close = (restore = true) => {
    clear();
    if (restore || menu.current?.contains(document.activeElement))
      trigger.current?.focus();
    setOpen(false);
  };
  const focus = (edge: 'first' | 'last') => {
    const buttons = menu.current?.querySelectorAll<HTMLButtonElement>(
      'button:not(:disabled)',
    );
    (edge === 'first' ? buttons?.[0] : buttons?.[buttons.length - 1])?.focus();
  };
  const leave = (event: PointerEvent) => {
    if (!hover || event.pointerType !== 'mouse' || keyboard.current) return;
    clear();
    timer.current = setTimeout(() => close(false), 150);
  };
  const keyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key === 'Tab') {
      close();
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    if (
      'focusGroup' in HTMLElement.prototype &&
      ['ArrowDown', 'ArrowUp'].includes(event.key)
    )
      return;
    event.preventDefault();
    keyboard.current = true;
    const buttons = [
      ...event.currentTarget.querySelectorAll<HTMLButtonElement>(
        'button:not(:disabled)',
      ),
    ];
    const current = buttons.indexOf(event.target as HTMLButtonElement);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? buttons.length - 1
          : (current + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) %
            buttons.length;
    buttons[next]?.focus();
  };
  return {
    open,
    trigger,
    menu,
    close,
    setOpen,
    menuProps: {
      ref: (node: HTMLDivElement | null) => {
        menu.current = node;
        // Native focusgroup owns roles and arrow navigation. Supply the existing
        // menu behavior only where the browser does not implement it.
        if (node && !('focusGroup' in HTMLElement.prototype)) {
          node.setAttribute('role', 'menu');
          node.querySelectorAll('button').forEach((button) => {
            button.setAttribute('role', 'menuitem');
            button.tabIndex = -1;
          });
        }
        if (node && enter.current) {
          focus(enter.current);
          enter.current = undefined;
        }
      },
      onPointerEnter: clear,
      onPointerLeave: leave,
      onBlur: (event: React.FocusEvent<HTMLDivElement>) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      },
      onKeyDown: keyDown,
    },
    triggerProps: {
      onPointerEnter: (event: PointerEvent) => {
        if (
          !hover ||
          event.pointerType !== 'mouse' ||
          event.currentTarget.hasAttribute('disabled')
        )
          return;
        clear();
        keyboard.current = false;
        setOpen(true);
      },
      onPointerLeave: leave,
      onBlur: (event: React.FocusEvent<HTMLButtonElement>) => {
        if (!menu.current?.contains(event.relatedTarget)) setOpen(false);
      },
      onClick: (event: React.MouseEvent) => {
        keyboard.current = event.detail === 0;
        enter.current = keyboard.current ? 'first' : undefined;
      },
      onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => {
        if (event.key === 'Tab' && open) close();
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        event.preventDefault();
        keyboard.current = true;
        clear();
        const edge = event.key === 'ArrowUp' ? 'last' : 'first';
        if (open) focus(edge);
        else {
          enter.current = edge;
          setOpen(true);
        }
      },
    },
  };
}
