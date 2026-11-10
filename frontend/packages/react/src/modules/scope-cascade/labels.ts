import { resolveLocale, type Locale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

export function resolveScopeCascadeLabels(locale: Locale) {
  return resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
}
