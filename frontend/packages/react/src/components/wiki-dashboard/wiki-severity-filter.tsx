import {
  Dropdown,
  DropdownItem,
} from '../../shared/primitives/dropdown/index.js';
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
  return (
    <Dropdown
      className="ov-wiki-dashboard__severity-popover"
      menuClassName="ov-wiki-dashboard__severity-menu"
      alignment="center"
      trigger={
        <button
          type="button"
          aria-label={`${t.severity}: ${t[value]}`}
          className="ov-wiki-dashboard__severity-filter"
        >
          <FilterIcon aria-hidden="true" />
          <span>{t.severity}</span>
          <span className="ov-wiki-dashboard__filter-value">{t[value]}</span>
          <ChevronIcon aria-hidden="true" />
        </button>
      }
    >
      {values.map((severity) => (
        <DropdownItem
          key={severity}
          role="menuitemradio"
          aria-checked={value === severity}
          onClick={() => onChange(severity)}
        >
          {t[severity]}
        </DropdownItem>
      ))}
    </Dropdown>
  );
}
