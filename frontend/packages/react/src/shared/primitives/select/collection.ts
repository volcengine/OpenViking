import { Children, cloneElement, isValidElement } from 'react';
import type { ReactNode } from 'react';
import { SelectOption } from './select-option.js';
import type { SelectOptionProps, SelectSelectedOption } from './types.js';

/** Inspect declarations through fragments and decorators without rewriting them. */
export function collectOptions(children: ReactNode): SelectSelectedOption[] {
  const options: SelectSelectedOption[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement<{ children?: ReactNode }>(child)) return;
    if (child.type === SelectOption) {
      const props = child.props as SelectOptionProps;
      options.push({
        value: props.value,
        label: props.label ?? props.children,
        disabled: props.disabled ?? false,
      });
    } else options.push(...collectOptions(child.props.children));
  });
  return options;
}

export function optionText(node: ReactNode): string {
  return Children.toArray(node)
    .map((child): string => {
      if (typeof child === 'string' || typeof child === 'number')
        return String(child);
      return isValidElement<{ children?: ReactNode }>(child)
        ? optionText(child.props.children)
        : '';
    })
    .join('');
}

/** Keep option decorators intact and remove empty wrappers after filtering. */
export function filterOptions(children: ReactNode, query: string): ReactNode {
  if (!query) return children;
  return Children.map(children, (child) => {
    if (!isValidElement<{ children?: ReactNode }>(child)) return child;
    if (child.type === SelectOption) {
      const props = child.props as SelectOptionProps;
      return optionText(props.label ?? props.children)
        .toLocaleLowerCase()
        .includes(query)
        ? child
        : null;
    }
    if (child.props.children === undefined) return child;
    const filtered = filterOptions(child.props.children, query);
    return Children.toArray(filtered).length
      ? cloneElement(child, {
          children: isValidElement(child.props.children)
            ? Children.toArray(filtered)[0]
            : filtered,
        })
      : null;
  });
}
