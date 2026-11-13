import { cloneElement, useCallback, useId, version } from 'react';
import type { HTMLAttributes, ReactElement, Ref } from 'react';
import { DropdownSurface } from './dropdown-surface.js';
import { DropdownContext } from './dropdown-context.js';
import { useDropdown } from './use-dropdown.js';
import type { DropdownProps } from './types.js';

function compose<E>(
  first: ((event: E) => void) | undefined,
  second: (event: E) => void,
) {
  return (event: E) => {
    first?.(event);
    second(event);
  };
}

/** Internal command menu. Children render unchanged, including item wrappers. */
export function Dropdown({
  trigger,
  children,
  triggerMode = 'click',
  disabled = false,
  placement = 'bottom',
  alignment = 'end',
  open,
  className,
  menuClassName,
  onOpenChange,
}: DropdownProps) {
  const controls = useDropdown(triggerMode === 'hover', open, onOpenChange);
  const id = useId();
  const child = trigger as ReactElement<
    HTMLAttributes<HTMLButtonElement> & { ref?: Ref<HTMLButtonElement> }
  >;
  const props = child.props;
  const childRef =
    Number.parseInt(version, 10) >= 19
      ? props.ref
      : (child as unknown as { ref?: Ref<HTMLButtonElement> }).ref;
  const ref = useCallback(
    (node: HTMLButtonElement | null) => {
      controls.trigger.current = node;
      if (typeof childRef === 'function') childRef(node);
      else if (childRef) childRef.current = node;
    },
    [childRef, controls.trigger],
  );
  const triggerId = props.id ?? `${id}-trigger`;
  const handlers = controls.triggerProps;
  const triggerNode = cloneElement(child, {
    ref,
    id: triggerId,
    'aria-haspopup': 'menu',
    'aria-expanded': controls.open && !disabled,
    'aria-controls': controls.open && !disabled ? `${id}-menu` : undefined,
    onPointerEnter: compose(props.onPointerEnter, handlers.onPointerEnter),
    onPointerLeave: compose(props.onPointerLeave, handlers.onPointerLeave),
    onBlur: compose(props.onBlur, handlers.onBlur),
    onClick: compose(props.onClick, handlers.onClick),
    onKeyDown: compose(props.onKeyDown, handlers.onKeyDown),
  });
  return (
    <>
      {triggerNode}
      {controls.open && !disabled ? (
        <DropdownSurface
          anchor={controls.trigger}
          placement={placement}
          alignment={alignment}
          className={className}
          onReady={controls.onReady}
        >
          <DropdownContext.Provider value={controls.close}>
            <div
              {...controls.menuProps}
              {...{ focusgroup: 'menu nomemory' }}
              role="menu"
              id={`${id}-menu`}
              className={['ov-dropdown__menu', menuClassName]
                .filter(Boolean)
                .join(' ')}
              tabIndex={-1}
              aria-labelledby={triggerId}
            >
              {children}
            </div>
          </DropdownContext.Provider>
        </DropdownSurface>
      ) : null}
    </>
  );
}
