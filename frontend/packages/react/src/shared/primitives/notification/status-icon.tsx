import type { NotificationType } from './types.js';
import { LoadingIcon } from '../loading-icon/loading-icon.js';

/** Status symbols for the notification variants. */
export function StatusIcon({ type }: { type: NotificationType }) {
  if (type === 'loading')
    return <LoadingIcon className="ov-notification__loading-icon" />;
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      aria-hidden="true"
    >
      {type === 'warning' ? (
        <path d="M12 3 23 21H1Z" fill="currentColor" />
      ) : (
        <circle cx="12" cy="12" r="10" fill="currentColor" />
      )}
      <g
        className="ov-notification__icon-mark"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {type === 'success' ? (
          <path d="m7 12 3 3 7-7" />
        ) : type === 'error' ? (
          <path d="m8 8 8 8m0-8-8 8" />
        ) : type === 'warning' ? (
          <path d="M12 10v4m0 3v.1" />
        ) : (
          <path d="M12 11v6m0-10v.1" />
        )}
      </g>
    </svg>
  );
}
