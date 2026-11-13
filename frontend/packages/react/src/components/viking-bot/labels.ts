import { useLocale } from '../../shared/locale-context.js';
import { renderErrors as renderEn } from './locales/en-US.js';
import { renderErrors as renderZh } from './locales/zh-CN.js';
import type { VikingBotLabels } from './model.js';
import { resolveLocale, type Locale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = { 'en-US': enUS, 'zh-CN': zhCN };

export const DEFAULT_LABELS: Required<VikingBotLabels> = enUS;

export function resolveVikingBotLabels(
  locale: Locale,
  labels?: VikingBotLabels,
): Required<VikingBotLabels> {
  return resolveLocale<Required<VikingBotLabels>>(LOCALES, locale, labels);
}

/** Private render-failure copy; independent of request errors and public label overrides. */
export function useRenderErrors(locale?: Parameters<typeof useLocale>[0]) {
  return useLocale(locale) === 'zh-CN' ? renderZh : renderEn;
}
