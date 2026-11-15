import { Select, SelectOption } from '../../shared/primitives/select/index.js';
import FilterIcon from '../../../../icons/svg/dashboards/severity-filter.svg?react';
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
    <Select<Severity>
      variant="simple"
      value={[value]}
      onChange={([severity]) => {
        if (severity) onChange(severity);
      }}
      popupClassName="ov-wiki-dashboard__severity-popover"
      listboxClassName="ov-wiki-dashboard__severity-menu"
      alignment="center"
      aria-label={`${t.severity}: ${t[value]}`}
      className="ov-wiki-dashboard__severity-filter"
      renderValue={(selected) => (
        <>
          <FilterIcon aria-hidden="true" />
          <span>{t.severity}</span>
          <span className="ov-wiki-dashboard__filter-value">
            {selected[0]?.label}
          </span>
        </>
      )}
    >
      {values.map((severity) => (
        <SelectOption key={severity} value={severity}>
          {t[severity]}
        </SelectOption>
      ))}
    </Select>
  );
}
