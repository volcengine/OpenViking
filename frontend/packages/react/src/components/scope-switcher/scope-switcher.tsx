import type { ScopeSwitcherProps } from './types.js';
import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
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

/** Layout and language scope only; selectors own data and interaction state. */
export function ScopeSwitcher({
  locale: localeProp,
  children,
  className = '',
  ...props
}: ScopeSwitcherProps) {
  const locale = useLocale(localeProp);
  return (
    <LocaleProvider locale={locale}>
      <div
        lang={locale}
        {...props}
        className={`ov-scope-switcher ${className}`}
      >
        {children}
      </div>
    </LocaleProvider>
  );
}
