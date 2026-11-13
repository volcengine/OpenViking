import { cloneElement, Fragment, useCallback, useId, version } from 'react';
import type { HTMLAttributes, ReactElement, Ref } from 'react';
import { Popover } from '../../../shared/primitives/popover/popover.js';
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

/** Grouped command rendering and interaction, independent of file previews. */
export function Dropdown({
  children,
  groups,
  disabled = false,
  trigger = 'hover',
  className,
  menuClassName,
}: DropdownProps) {
  const controls = useDropdown(trigger === 'hover');
  const id = useId();
  const child = children as ReactElement<
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
  const visibleGroups = groups.filter((group) => group.length > 0);
  const handlers = controls.triggerProps;
  const triggerNode = cloneElement(child, {
    ref,
    id: triggerId,
    'aria-haspopup': 'menu',
    'aria-controls': controls.open && !disabled ? `${id}-menu` : undefined,
    onPointerEnter: compose(props.onPointerEnter, handlers.onPointerEnter),
    onPointerLeave: compose(props.onPointerLeave, handlers.onPointerLeave),
    onBlur: compose(props.onBlur, handlers.onBlur),
    onClick: compose(props.onClick, handlers.onClick),
    onKeyDown: compose(props.onKeyDown, handlers.onKeyDown),
  });
  if (visibleGroups.length === 0) return children;
  return (
    <Popover
      trigger="click"
      placement="bottom"
      alignment="end"
      open={controls.open && !disabled}
      disabled={disabled}
      role="presentation"
      className={['ov-dropdown', className].filter(Boolean).join(' ')}
      onOpenChange={controls.setOpen}
      content={
        <div
          {...controls.menuProps}
          {...{ focusgroup: 'menu nomemory' }}
          id={`${id}-menu`}
          className={['ov-dropdown__menu', menuClassName]
            .filter(Boolean)
            .join(' ')}
          tabIndex={-1}
          aria-labelledby={triggerId}
        >
          {visibleGroups.map((group, index) => (
            <Fragment key={index}>
              {index > 0 ? <hr /> : null}
              {group.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className={['ov-dropdown__item', item.className]
                    .filter(Boolean)
                    .join(' ')}
                  disabled={disabled || item.disabled}
                  onPointerMove={(event) =>
                    event.currentTarget.focus({ preventScroll: true })
                  }
                  onClick={() => {
                    controls.close();
                    void item.onClick();
                  }}
                >
                  <span className="ov-dropdown__icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  {item.label}
                </button>
              ))}
            </Fragment>
          ))}
        </div>
      }
    >
      {triggerNode}
    </Popover>
  );
}
