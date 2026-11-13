import { useCallback, useState } from 'react';
import type { RefObject } from 'react';
import { createPortal } from 'react-dom';
import { useOverlayPosition } from '../overlay/use-overlay-position.js';
import type { DropdownProps } from './types.js';

export function DropdownSurface({
  anchor,
  placement = 'bottom',
  alignment,
  className,
  onReady,
  children,
}: Pick<DropdownProps, 'placement' | 'alignment' | 'className' | 'children'> & {
  anchor: RefObject<HTMLButtonElement | null>;
  onReady: () => void;
}) {
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const position = useOverlayPosition(
    true,
    anchor,
    surface,
    placement,
    alignment,
  );
  const surfaceRef = useCallback(
    (node: HTMLDivElement | null) => {
      setSurface(node);
      // Promote the committed DOM before focusing items, including inside a modal.
      // Removal automatically releases the browser's top-layer entry.
      if (node) {
        node.showPopover?.();
        onReady();
      }
    },
    [onReady],
  );
  if (typeof document === 'undefined') return null;
  return createPortal(
    <div
      ref={surfaceRef}
      popover="manual"
      className={['ov-popover', 'ov-dropdown', className]
        .filter(Boolean)
        .join(' ')}
      data-placement={position.placement}
      style={{ top: position.top, left: position.left }}
    >
      {children}
    </div>,
    document.body,
  );
}
