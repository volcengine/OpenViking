import type { ScopeSwitcherProps } from './types.js';
export { ScopeSelector } from './scope-selector.js';
export { SCOPE_SWITCHER_SEARCH_THRESHOLD } from './types.js';
export type {
  ScopeSelectorBaseProps,
  ScopeSelectorFixedProps,
  ScopeSelectorProps,
  ScopeSelectorSelectableProps,
  ScopeSwitcherActions,
  ScopeSwitcherData,
  ScopeSwitcherIdentity,
  ScopeSwitcherLabels,
  ScopeSwitcherOption,
  ScopeSwitcherPanelContent,
  ScopeSwitcherProps,
  ScopeSwitcherScrollMetrics,
  ScopeSwitcherSlots,
  ScopeSwitcherState,
  ScopeSwitcherTrigger,
} from './types.js';

/** No shared state, data aggregation or subscription in the layout root. */
export function ScopeSwitcher({
  children,
  className = '',
  ...props
}: ScopeSwitcherProps) {
  return (
    <div {...props} className={`ov-scope-switcher ${className}`}>
      {children}
    </div>
  );
}
