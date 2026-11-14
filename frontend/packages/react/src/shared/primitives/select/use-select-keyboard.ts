import { useCallback, useRef } from 'react';
import type { KeyboardEvent } from 'react';
import type { SelectValue } from './types.js';
import type { useSelect } from './use-select.js';

export function useSelectKeyboard<V extends SelectValue>(
  state: ReturnType<typeof useSelect<V>>,
) {
  const { listbox, footer, search, open, setOpen, close } = state;
  const pending = useRef<'first' | 'last' | undefined>(undefined);
  const typed = useRef({ text: '', at: 0 });
  const footerActions = () =>
    [
      ...(footer.current?.querySelectorAll<HTMLElement>(
        'button, a[href], input, select, textarea, [tabindex], [contenteditable="true"]',
      ) ?? []),
    ]
      .filter((element) => {
        if (
          element.tabIndex < 0 ||
          element.matches(':disabled, [type="hidden"]') ||
          element.closest('[hidden], [inert]')
        )
          return false;
        for (
          let node: HTMLElement | null = element;
          node && node !== footer.current;
          node = node.parentElement
        ) {
          if (getComputedStyle(node).display === 'none') return false;
        }
        return getComputedStyle(element).visibility === 'visible';
      })
      .sort((a, b) => (a.tabIndex || Infinity) - (b.tabIndex || Infinity));
  const focus = useCallback(
    (edge: 'first' | 'last') => {
      const options = [
        ...(listbox.current?.querySelectorAll<HTMLButtonElement>(
          '.ov-select__option:not(:disabled)',
        ) ?? []),
      ];
      const selected = options.find(
        (option) => option.getAttribute('aria-selected') === 'true',
      );
      const next = selected ?? (edge === 'first' ? options[0] : options.at(-1));
      (next ?? listbox.current)?.focus();
      next?.scrollIntoView?.({ block: 'nearest' });
    },
    [listbox],
  );
  const onReady = useCallback(() => {
    if (search.current) search.current.focus();
    else focus(pending.current ?? 'first');
    pending.current = undefined;
  }, [focus, search]);
  const escape = (event: KeyboardEvent) => {
    if (!open || event.key !== 'Escape') return false;
    event.preventDefault();
    event.stopPropagation();
    close();
    return true;
  };
  const triggerKey = (event: KeyboardEvent<HTMLElement>) => {
    if (
      event.defaultPrevented ||
      event.nativeEvent.isComposing ||
      escape(event)
    )
      return;
    if (event.key === 'Tab') {
      close(false);
      return;
    }
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
      event.preventDefault();
      if (open && (event.key === 'Enter' || event.key === ' ')) {
        close();
        return;
      }
      const edge = event.key === 'ArrowUp' ? 'last' : 'first';
      if (open) focus(edge);
      else {
        pending.current = edge;
        setOpen(true);
      }
    }
  };
  const listKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (
      event.defaultPrevented ||
      event.nativeEvent.isComposing ||
      escape(event)
    )
      return;
    if (event.key === 'Tab') {
      if (event.shiftKey && search.current) {
        event.preventDefault();
        search.current.focus();
        return;
      }
      const action = !event.shiftKey && footerActions()[0];
      if (action) {
        event.preventDefault();
        action.focus();
      } else close();
      return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const options = [
      ...event.currentTarget.querySelectorAll<HTMLButtonElement>(
        '.ov-select__option:not(:disabled)',
      ),
    ];
    const index = options.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === 'Enter' || event.key === ' ') {
      if (index >= 0) {
        event.preventDefault();
        options[index]?.click();
      }
      return;
    }
    let next: HTMLButtonElement | undefined;
    if (event.key === 'Home') next = options[0];
    else if (event.key === 'End') next = options.at(-1);
    else if (event.key === 'ArrowDown') next = options[index + 1];
    else if (event.key === 'ArrowUp') {
      next = options[index - 1];
      if (!next && search.current) {
        event.preventDefault();
        search.current.focus();
      }
    } else if (event.key.length === 1) {
      const now = Date.now();
      const char = event.key.toLocaleLowerCase();
      const text =
        now - typed.current.at < 500 ? typed.current.text + char : char;
      typed.current = { text, at: now };
      const query = [...text].every((item) => item === char) ? char : text;
      next = [...options.slice(index + 1), ...options.slice(0, index + 1)].find(
        (option) =>
          option.textContent?.trim().toLocaleLowerCase().startsWith(query),
      );
    }
    if (next) {
      event.preventDefault();
      next.focus();
      next.scrollIntoView?.({ block: 'nearest' });
    }
  };
  const footerKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (
      event.defaultPrevented ||
      event.nativeEvent.isComposing ||
      escape(event)
    )
      return;
    if (event.key === 'Tab') {
      const actions = footerActions();
      const boundary = event.shiftKey ? actions[0] : actions.at(-1);
      // Preserve native navigation between footer actions; only bridge the
      // first/last action across this portaled panel's logical boundaries.
      if (document.activeElement !== boundary) return;
      if (event.shiftKey) {
        event.preventDefault();
        focus('last');
      } else {
        close();
      }
    }
  };
  const searchKey = (event: KeyboardEvent<HTMLElement>) => {
    if (
      event.defaultPrevented ||
      event.nativeEvent.isComposing ||
      escape(event)
    )
      return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      focus(event.key === 'ArrowDown' ? 'first' : 'last');
    } else if (event.key === 'Tab') {
      if (event.shiftKey) close();
      else {
        event.preventDefault();
        focus('first');
      }
    } else if (event.key === 'Enter') event.preventDefault();
  };
  return { onReady, triggerKey, listKey, footerKey, searchKey };
}
