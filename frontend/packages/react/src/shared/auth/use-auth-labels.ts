import { useLocale } from '../locale-context.js';
import { resolveLocale, type Locale } from '../locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';
import type { AuthLabels, AuthMessages } from './types.js';

const MESSAGES = { 'en-US': enUS, 'zh-CN': zhCN };

export function useAuthLabels(locale?: Locale, labels?: Partial<AuthLabels>) {
  return resolveLocale<AuthMessages>(MESSAGES, useLocale(locale), labels);
}
