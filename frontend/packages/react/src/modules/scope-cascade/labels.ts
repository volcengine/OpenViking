import { useLocale } from '../../shared/locale-context.js';
import { renderErrors as renderEn } from './locales/en-US.js';
import { renderErrors as renderZh } from './locales/zh-CN.js';
import { resolveLocale, type Locale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

export function resolveScopeCascadeLabels(locale: Locale) {
  return resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
}

/** Private render-failure copy; independent of request errors and public label overrides. */
export function useRenderErrors(locale?: Parameters<typeof useLocale>[0]) {
  return useLocale(locale) === 'zh-CN' ? renderZh : renderEn;
}
