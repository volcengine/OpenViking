import type { FileBrowserActionBarLabels } from './types.js';
import { resolveLocale, type Locale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = { 'en-US': enUS, 'zh-CN': zhCN };

export function resolveFileBrowserActionBarLabels(
  locale: Locale,
  labels?: FileBrowserActionBarLabels,
): Required<FileBrowserActionBarLabels> {
  return resolveLocale(LOCALES, locale, labels);
}
