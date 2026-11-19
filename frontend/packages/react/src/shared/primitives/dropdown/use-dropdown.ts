import { useCallback, useEffect, useRef, useState } from 'react';
import { useViewActive } from '../../view-activity.js';
import type { KeyboardEvent, PointerEvent } from 'react';

function menuItems(menu: HTMLDivElement | null) {
  return Array.from(
    menu?.querySelectorAll<HTMLButtonElement>(
      '.ov-dropdown__item:not(:disabled)',
    ) ?? [],
  ).filter((item) => item.closest('[role="menu"]') === menu);
}

/** Pointer hover and keyboard navigation share one controlled menu. */
export function useDropdown(
  hover: boolean,
  controlledOpen?: boolean,
  onOpenChange?: (open: boolean) => void,
  nested = false,
) {
  const active = useViewActive();
  const currentActivity = useRef(active);
  currentActivity.current = active;
  const [localOpen, setLocalOpen] = useState(false);
  if (!active && localOpen) setLocalOpen(false);
  const open = active && (controlledOpen ?? localOpen);
  const setOpen = useCallback(
    (next: boolean) => {
      if (next && !active) return;
      setLocalOpen(next);
      onOpenChange?.(next);
    },
    [active, onOpenChange],
  );
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  const enter = useRef<'first' | 'last' | undefined>(undefined);
  const keyboard = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const clear = () => {
    clearTimeout(timer.current);
    timer.current = undefined;
  };
  // The pointerleave timer bridges the gap to the portal. Unmount is not a
  // pointer event, so hiding its owning view or unmounting must cancel the timer.
  useEffect(() => {
    if (!active) clearTimeout(timer.current);
    return () => clearTimeout(timer.current);
  }, [active]);
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
    const outsidePointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!trigger.current?.contains(target) && !menu.current?.contains(target))
        setOpen(false);
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      trigger.current?.focus();
      setOpen(false);
    };
    document.addEventListener('focusin', outsideFocus);
    document.addEventListener('mousedown', outsidePointer);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('focusin', outsideFocus);
      document.removeEventListener('mousedown', outsidePointer);
      document.removeEventListener('keydown', escape);
    };
  }, [open, setOpen]);
  const close = (restore = true) => {
    clear();
    if (
      currentActivity.current &&
      (restore || menu.current?.contains(document.activeElement))
    )
      trigger.current?.focus();
    setOpen(false);
  };
  const focus = useCallback((edge: 'first' | 'last') => {
    const buttons = menuItems(menu.current);
    (edge === 'first' ? buttons[0] : buttons.at(-1))?.focus();
  }, []);
  const onReady = useCallback(() => {
    if (enter.current) focus(enter.current);
    enter.current = undefined;
  }, [focus]);
  const leave = (event: PointerEvent) => {
    if (!hover || event.pointerType !== 'mouse' || keyboard.current) return;
    clear();
    timer.current = setTimeout(() => close(false), 150);
  };
  const keyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (
      (event.target as HTMLElement).closest('[role="menu"]') !==
      event.currentTarget
    )
      return;
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
    if (
      event.defaultPrevented ||
      !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)
    )
      return;
    if (
      'focusGroup' in HTMLElement.prototype &&
      ['ArrowDown', 'ArrowUp'].includes(event.key)
    )
      return;
    event.preventDefault();
    keyboard.current = true;
    const buttons = menuItems(event.currentTarget);
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
    onReady,
    menuProps: {
      ref: (node: HTMLDivElement | null) => {
        menu.current = node;
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
        setOpen(!open);
      },
      onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => {
        if (open && event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          close();
          return;
        }
        if (event.key === 'Tab' && open) close();
        if (
          (nested && !open) ||
          (event.key !== 'ArrowDown' && event.key !== 'ArrowUp')
        )
          return;
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
