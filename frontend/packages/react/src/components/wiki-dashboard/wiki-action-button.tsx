import type { ReactNode } from 'react';
import { Tooltip } from '../../shared/components/tooltip/tooltip.js';

export function WikiActionButton({
  label,
  hint,
  onClick,
  children,
  ...aria
}: {
  label: string;
  hint: string;
  onClick?: () => void;
  children: ReactNode;
  'aria-expanded'?: boolean;
  'aria-controls'?: string;
}) {
  return (
    <Tooltip content={hint}>
      <button
        {...aria}
        type="button"
        aria-label={label}
        aria-disabled={!onClick || undefined}
        aria-description={!onClick ? hint : undefined}
        onClick={() => onClick?.()}
      >
        {children}
      </button>
    </Tooltip>
  );
}
