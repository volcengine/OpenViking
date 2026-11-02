import type { HTMLAttributes } from 'react';
import styles from './status-badge.module.css';

export type StatusBadgeStatus = 'ready' | 'processing' | 'error';

export interface StatusBadgeProps extends HTMLAttributes<HTMLSpanElement> {
  /** The state to display. Text accompanies the color indicator. */
  status: StatusBadgeStatus;
}

const labels: Record<StatusBadgeStatus, string> = {
  ready: 'Ready',
  processing: 'Processing',
  error: 'Error',
};

/** Displays a status with a color indicator and an optional custom label. */
export function StatusBadge({
  status,
  children,
  className,
  ...props
}: StatusBadgeProps) {
  return (
    <span
      {...props}
      className={[styles.badge, className].filter(Boolean).join(' ')}
      data-status={status}
    >
      <span className={styles.indicator} aria-hidden="true" />
      <span>{children ?? labels[status]}</span>
    </span>
  );
}
