import { forwardRef, useCallback, useContext } from 'react';
import { DropdownContext } from './dropdown-context.js';
import type { DropdownItemProps } from './types.js';

/** A command button; forwards refs and events so wrappers can decorate it. */
export const DropdownItem = forwardRef<HTMLButtonElement, DropdownItemProps>(
  function DropdownItem(
    { className, closeOnClick = true, onClick, ...props },
    ref,
  ) {
    const close = useContext(DropdownContext);
    const itemRef = useCallback(
      (node: HTMLButtonElement | null) => {
        if (node) {
          // Some focusgroup implementations navigate without inferring roles.
          if (!node.hasAttribute('role')) node.setAttribute('role', 'menuitem');
          if (!('focusGroup' in HTMLElement.prototype)) node.tabIndex = -1;
        }
        if (typeof ref === 'function') ref(node);
        else if (ref) ref.current = node;
      },
      [ref],
    );
    return (
      <button
        {...props}
        ref={itemRef}
        type="button"
        className={['ov-dropdown__item', className].filter(Boolean).join(' ')}
        onClick={(event) => {
          if (closeOnClick) close?.();
          onClick?.(event);
        }}
      />
    );
  },
);
