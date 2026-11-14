import { useLocale } from '../../shared/locale-context.js';
import { renderErrors as renderEn } from './locales/en-US.js';
import { renderErrors as renderZh } from './locales/zh-CN.js';
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
  // 终端空态由 CommandConsole 内置渲染并自带默认文案；这里只转发宿主显式覆盖的三条旧字段，
  // 避免 Console 复制一份默认值而挡住 CommandConsole 后续的文案更新。
  // CommandConsole renders the terminal empty state with its own defaults; forward only the
  // legacy fields the host overrides explicitly, so Console never shadows later
  // CommandConsole copy changes with a duplicate default.
  const legacy = {
    emptyTitle: overrides?.commandTitle,
    emptyDescription: overrides?.commandDescription,
    quickStart: overrides?.quickStart,
  };
  return {
    ...merged,
    commandConsole: mergeLabels<NonNullable<ConsoleLabels['commandConsole']>>(
      Object.fromEntries(
        Object.entries(legacy).filter(([, value]) => value != null),
      ),
      overrides?.commandConsole,
    ),
    vikingBot: mergeLabels<NonNullable<ConsoleLabels['vikingBot']>>(
      { welcomeDescription: overrides?.description ?? defaults.description },
      overrides?.vikingBot,
    ),
  };
}

/** Private render-failure copy; independent of request errors and public label overrides. */
export function useRenderErrors(locale?: Parameters<typeof useLocale>[0]) {
  return useLocale(locale) === 'zh-CN' ? renderZh : renderEn;
}
