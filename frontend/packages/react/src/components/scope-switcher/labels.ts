import type { ScopeSwitcherLabels } from './types.js';
import { resolveLocale, type Locale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = { 'en-US': enUS, 'zh-CN': zhCN };

export function resolveScopeLabels(
  locale: Locale = 'en-US',
  labels?: ScopeSwitcherLabels,
): Required<ScopeSwitcherLabels> {
  const defaults = resolveLocale(LOCALES, locale, labels);
  return {
    ...defaults,
    noMatch: labels?.noMatch ?? labels?.empty ?? defaults.noMatch,
  };
}
