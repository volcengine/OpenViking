import {
  resolveLocale,
  mergeLabels,
  type Locale,
} from '../../shared/locale.js';
import type { ConsoleLabels } from './types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

export function resolveConsoleLocale(locale: Locale) {
  return resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
}

export function consoleLabels(locale: Locale, overrides?: ConsoleLabels) {
  const defaults = resolveConsoleLocale(locale).labels;
  return {
    ...mergeLabels(defaults, overrides),
    tui: overrides?.tui,
    vikingBot: mergeLabels<NonNullable<ConsoleLabels['vikingBot']>>(
      { welcomeDescription: overrides?.description ?? defaults.description },
      overrides?.vikingBot,
    ),
  };
}
