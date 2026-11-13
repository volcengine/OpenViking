import type { FilePreviewLabels } from './types.js';
import { resolveLocale, type Locale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = { 'en-US': enUS, 'zh-CN': zhCN };

export const DEFAULT_LABELS: Required<FilePreviewLabels> = enUS;

export function resolveFilePreviewLabels(
  locale: Locale,
  labels?: FilePreviewLabels,
): Required<FilePreviewLabels> {
  return resolveLocale<Required<FilePreviewLabels>>(LOCALES, locale, labels);
}
