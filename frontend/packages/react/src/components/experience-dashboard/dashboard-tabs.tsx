import { useId } from 'react';
import type { ReactNode } from 'react';

/**
 * 看板的页签：一条带图标的页签栏加一块面板。
 *
 * 方向键、Home 与 End 在页签之间移动焦点并立即切换，符合 WAI-ARIA 的 tabs 模式。
 * 图标由调用方随标题一起给出，这个组件只负责结构与键盘行为。
 *
 * The board's tabs: an icon-bearing tab bar and a panel.
 *
 * Arrow keys, Home and End move focus between tabs and switch immediately, following
 * the WAI-ARIA tabs pattern. The caller supplies the icon with the title; this
 * component owns the structure and keyboard behaviour.
 */
export function DashboardTabs<T extends string>({
  tabs,
  active,
  label,
  onChange,
  children,
}: {
  tabs: readonly { key: T; title: string; icon?: ReactNode }[];
  active: T;
  label: string;
  onChange: (key: T) => void;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <>
      <div
        className="ov-experience-dashboard__tabs"
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
            {tab.icon}
            <span className="ov-experience-dashboard__tab-title">
              {tab.title}
            </span>
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`${id}-panel`}
        aria-labelledby={`${id}-${active}`}
        className="ov-experience-dashboard__panel"
      >
        {children}
      </div>
    </>
  );
}
