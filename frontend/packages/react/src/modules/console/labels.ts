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
  const merged = mergeLabels(defaults, overrides);
  return {
    ...merged,
    // 终端空态由 Tui 内置渲染，这三条文案转交 Tui 的对应字段。
    tui: mergeLabels<NonNullable<ConsoleLabels['tui']>>(
      {
        emptyTitle: merged.commandTitle,
        emptyDescription: merged.commandDescription,
        quickStart: merged.quickStart,
      },
      overrides?.tui,
    ),
    vikingBot: mergeLabels<NonNullable<ConsoleLabels['vikingBot']>>(
      { welcomeDescription: overrides?.description ?? defaults.description },
      overrides?.vikingBot,
    ),
  };
}
