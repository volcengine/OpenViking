import { useLocale } from '../../shared/locale-context.js';
import { renderErrors as renderEn } from './locales/en-US.js';
import { renderErrors as renderZh } from './locales/zh-CN.js';
import type { CommandConsoleLabels } from './types.js';
import { resolveLocale, type Locale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = { 'en-US': enUS, 'zh-CN': zhCN };

export type ResolvedCommandConsoleLabels = Required<CommandConsoleLabels>;

export const DEFAULT_COMMAND_CONSOLE_LABELS: ResolvedCommandConsoleLabels =
  enUS;

export function resolveCommandConsoleLabels(
  labels?: CommandConsoleLabels,
  locale: Locale = 'en-US',
): ResolvedCommandConsoleLabels {
  return resolveLocale<ResolvedCommandConsoleLabels>(LOCALES, locale, labels);
}

/** Private render-failure copy; independent of request errors and public label overrides. */
export function useRenderErrors(locale?: Parameters<typeof useLocale>[0]) {
  return useLocale(locale) === 'zh-CN' ? renderZh : renderEn;
}
