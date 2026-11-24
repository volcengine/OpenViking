import { createElement } from 'react';
import { useConfig } from '@ark-design/ui/config-provider';
import type { MDRendererHeaderProps } from '@ark-design/ui/markdown-renderer/full';

/** Keep sanitized explicit anchor IDs while retaining the renderer's heading layout. */
export function DirectoryHeading({
  className,
  children,
  node,
}: MDRendererHeaderProps) {
  const prefix = useConfig().getPrefixCls('markdown');
  const id = node.properties.id;
  return createElement(
    node.tagName || 'h1',
    {
      className: `${prefix}__heading${className ? ` ${className}` : ''}`,
      id: typeof id === 'string' ? id : undefined,
    },
    children,
  );
}
