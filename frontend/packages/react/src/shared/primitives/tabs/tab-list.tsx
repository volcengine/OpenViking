import type { ReactNode } from 'react';
import { useTabIndicator } from './use-tab-indicator.js';

/** The underline and keyboard behavior shared with directory summary levels. */
export function TabList({
  id,
  items,
  activeKey,
  onChange,
  label,
  className,
  tabClassName,
  indicatorClassName,
  manual = false,
}: {
  id: string;
  items: readonly { key: string; label?: ReactNode; disabled?: boolean }[];
  activeKey: string;
  onChange: (key: string) => void;
  label?: string;
  className?: string;
  tabClassName?: string;
  indicatorClassName?: string;
  manual?: boolean;
}) {
  const { ref, indicator } = useTabIndicator(
    '[aria-selected="true"]',
    activeKey,
    items.length,
  );
  return (
    <div
      ref={ref}
      className={['ov-tabs', className].filter(Boolean).join(' ')}
      role="tablist"
      aria-label={label}
      tabIndex={-1}
      onKeyDown={(event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
          return;
        const buttons = Array.from(
          event.currentTarget.querySelectorAll<HTMLButtonElement>(
            '[role="tab"]:not(:disabled)',
          ),
        );
        if (!buttons.length) return;
        const current = buttons.indexOf(event.target as HTMLButtonElement);
        const next =
          event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? buttons.length - 1
              : (current +
                  (event.key === 'ArrowRight' ? 1 : -1) +
                  buttons.length) %
                buttons.length;
        const target = buttons[next];
        if (!target) return;
        event.preventDefault();
        target.focus();
        if (!manual) target.click();
      }}
    >
      {items.map((item) => (
        <button
          key={item.key}
          id={`${id}-${item.key}`}
          type="button"
          role="tab"
          aria-selected={item.key === activeKey}
          tabIndex={item.key === activeKey ? 0 : -1}
          disabled={item.disabled}
          data-active={item.key === activeKey || undefined}
          className={['ov-tabs__tab', tabClassName].filter(Boolean).join(' ')}
          onClick={() => onChange(item.key)}
        >
          {item.label}
        </button>
      ))}
      <span
        aria-hidden="true"
        className={['ov-tabs__indicator', indicatorClassName]
          .filter(Boolean)
          .join(' ')}
        style={{
          transform: `translateX(${indicator.left}px)`,
          width: indicator.width,
        }}
      />
    </div>
  );
}
