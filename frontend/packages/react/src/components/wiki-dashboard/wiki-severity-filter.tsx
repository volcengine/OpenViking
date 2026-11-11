import { useCallback, useId, useRef, useState } from 'react';
import { Popover } from '../../shared/components/popover/popover.js';
import FilterIcon from '../../../../icons/svg/dashboards/severity-filter.svg?react';
import ChevronIcon from '../../../../icons/svg/dashboards/filter-chevron.svg?react';
import type { WikiConflict, WikiDashboardLabels } from './types.js';

type Severity = WikiConflict['severity'] | 'all';
const values: readonly Severity[] = ['all', 'high', 'medium', 'low'];

export function WikiSeverityFilter({
  value,
  onChange,
  labels: t,
}: {
  value: Severity;
  onChange: (value: Severity) => void;
  labels: WikiDashboardLabels;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const id = useId();
  const focusSelected = useCallback((node: HTMLDivElement | null) => {
    node?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
  }, []);
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  return (
    <Popover
      trigger="click"
      placement="bottom"
      open={open}
      onOpenChange={setOpen}
      role="presentation"
      className="ov-wiki-dashboard__severity-popover"
      content={
        <div
          ref={focusSelected}
          id={id}
          role="listbox"
          tabIndex={-1}
          aria-label={t.severity}
          className="ov-wiki-dashboard__severity-menu"
          onBlur={(event) => {
            if (
              !event.currentTarget.contains(event.relatedTarget) &&
              event.relatedTarget !== trigger.current
            )
              setOpen(false);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape' || event.key === 'Tab') {
              if (event.key === 'Escape') event.preventDefault();
              close();
              return;
            }
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key))
              return;
            event.preventDefault();
            const options = [
              ...event.currentTarget.querySelectorAll<HTMLButtonElement>(
                '[role="option"]',
              ),
            ];
            const index = options.indexOf(event.target as HTMLButtonElement);
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? options.length - 1
                  : (index +
                      (event.key === 'ArrowDown' ? 1 : -1) +
                      options.length) %
                    options.length;
            options[next]?.focus();
          }}
        >
          {values.map((severity) => (
            <button
              key={severity}
              type="button"
              role="option"
              aria-selected={value === severity}
              tabIndex={value === severity ? 0 : -1}
              onClick={() => {
                onChange(severity);
                close();
              }}
            >
              {t[severity]}
            </button>
          ))}
        </div>
      }
    >
      <button
        type="button"
        aria-haspopup="listbox"
        aria-controls={open ? id : undefined}
        aria-expanded={open}
        aria-label={`${t.severity}: ${t[value]}`}
        className="ov-wiki-dashboard__severity-filter"
        onClick={(event) => {
          trigger.current = event.currentTarget;
        }}
        onKeyDown={(event) => {
          trigger.current = event.currentTarget;
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <FilterIcon aria-hidden="true" />
        <span>{t.severity}</span>
        <span className="ov-wiki-dashboard__filter-value">{t[value]}</span>
        <ChevronIcon aria-hidden="true" />
      </button>
    </Popover>
  );
}
