import { useCallback, useEffect, useRef, useState } from 'react';
import type { SelectProps, SelectValue } from './types.js';

export function useSelect<V extends SelectValue>(props: SelectProps<V>) {
  const {
    value,
    defaultValue = [],
    multiple,
    disabled,
    open: controlledOpen,
    onChange,
    onOpenChange,
  } = props;
  const [localValue, setLocalValue] = useState<readonly V[]>(defaultValue);
  const [localOpen, setLocalOpen] = useState(false);
  if (disabled && localOpen) setLocalOpen(false);
  const values = multiple
    ? (value ?? localValue)
    : (value ?? localValue).slice(0, 1);
  const open = !disabled && (controlledOpen ?? localOpen);
  const trigger = useRef<HTMLElement | null>(null);
  const anchor = useRef<HTMLElement | null>(null);
  const panel = useRef<HTMLDivElement | null>(null);
  const listbox = useRef<HTMLDivElement | null>(null);
  const footer = useRef<HTMLDivElement | null>(null);
  const search = useRef<HTMLInputElement | null>(null);
  const setOpen = useCallback(
    (next: boolean) => {
      if (disabled || next === open) return;
      if (controlledOpen === undefined) setLocalOpen(next);
      onOpenChange?.(next);
    },
    [disabled, open, controlledOpen, onOpenChange],
  );
  const close = (restore = true) => {
    if (restore) trigger.current?.focus();
    setOpen(false);
  };
  const update = (next: V[]) => {
    if (disabled) return;
    if (value === undefined) setLocalValue(next);
    if (
      next.length !== values.length ||
      next.some((item, index) => item !== values[index])
    )
      onChange?.(next);
  };
  const choose = (item: SelectValue) => {
    const selected = item as V;
    if (!multiple) close();
    update(
      multiple
        ? values.includes(selected)
          ? values.filter((v) => v !== selected)
          : [...values, selected]
        : [selected],
    );
  };
  // Browser focus and outside pointer events can leave the portal independently of
  // React events. Subscribe only while open and remove both listeners on cleanup.
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      const target = event.target as Node;
      if (!anchor.current?.contains(target) && !panel.current?.contains(target))
        setOpen(false);
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('focusin', outside);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('focusin', outside);
    };
  }, [open, setOpen]);
  return {
    values,
    open,
    trigger,
    anchor,
    panel,
    listbox,
    footer,
    search,
    setOpen,
    close,
    choose,
    update,
  };
}
