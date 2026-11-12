import { useId } from 'react';
import type { ReactNode } from 'react';

export function DashboardTabs<T extends string>({
  tabs,
  active,
  label,
  onChange,
  children,
}: {
  tabs: readonly { key: T; title: string }[];
  active: T;
  label: string;
  onChange: (key: T) => void;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <>
      <div
        className="ov-dashboard-tabs"
        role="tablist"
        tabIndex={-1}
        aria-label={label}
        onKeyDown={(event) => {
          const delta =
            event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
          if (!delta && event.key !== 'Home' && event.key !== 'End') return;
          event.preventDefault();
          const index =
            event.key === 'Home'
              ? 0
              : event.key === 'End'
                ? tabs.length - 1
                : (tabs.findIndex((tab) => tab.key === active) +
                    delta +
                    tabs.length) %
                  tabs.length;
          onChange(tabs[index]!.key);
          const buttons =
            event.currentTarget.querySelectorAll<HTMLButtonElement>(
              '[role="tab"]',
            );
          buttons[index]?.focus();
        }}
      >
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            id={`${id}-${tab.key}`}
            role="tab"
            aria-selected={active === tab.key}
            aria-controls={`${id}-panel`}
            tabIndex={active === tab.key ? 0 : -1}
            onClick={() => onChange(tab.key)}
          >
            {tab.title}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`${id}-panel`}
        aria-labelledby={`${id}-${active}`}
        className="ov-dashboard-tabpanel"
      >
        {children}
      </div>
    </>
  );
}
