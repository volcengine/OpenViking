import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, RefObject } from 'react';
import { createPortal } from 'react-dom';
import { useOverlayPosition } from '../overlay/use-overlay-position.js';
import { useModalFocusShard } from '../modal/focus-shards.js';
import type { DropdownProps } from './types.js';

export function DropdownSurface({
  anchor,
  placement = 'bottom',
  alignment,
  className,
  onReady,
  style,
  matchAnchorWidth,
  surfaceRef: externalSurface,
  children,
}: Pick<DropdownProps, 'placement' | 'alignment' | 'className' | 'children'> & {
  anchor: RefObject<HTMLElement | null>;
  onReady: () => void;
  style?: CSSProperties;
  matchAnchorWidth?: boolean;
  surfaceRef?: RefObject<HTMLDivElement | null>;
}) {
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const focusShard = useRef<HTMLDivElement | null>(null);
  const registerFocusShard = useModalFocusShard();
  // The committed portal is outside the modal DOM. Register it with FocusLock's
  // browser focus scope after mount; render cannot register an uncommitted node,
  // and unmount has no user event, so the returned cleanup unregisters the shard.
  useEffect(() => registerFocusShard?.(focusShard), [registerFocusShard]);
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
      focusShard.current = node;
      if (externalSurface) externalSurface.current = node;
      // Promote the committed DOM before focusing items, including inside a modal.
      // Removal automatically releases the browser's top-layer entry.
      if (node) {
        node.showPopover?.();
        onReady();
      }
    },
    [onReady, externalSurface],
  );
  return createPortal(
    <div
      ref={surfaceRef}
      popover="manual"
      className={['ov-popover', 'ov-dropdown', className]
        .filter(Boolean)
        .join(' ')}
      data-placement={position.placement}
      style={{
        top: position.top,
        left: position.left,
        minWidth: matchAnchorWidth
          ? `min(${position.anchorWidth ?? 0}px, calc(100vw - 16px))`
          : undefined,
        ...style,
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
