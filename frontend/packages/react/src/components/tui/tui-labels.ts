import type { TuiLabels } from './types.js';
import { resolveLocale, type Locale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = { 'en-US': enUS, 'zh-CN': zhCN };

export type ResolvedTuiLabels = Required<TuiLabels>;

export const DEFAULT_TUI_LABELS: ResolvedTuiLabels = enUS;

export function resolveTuiLabels(
  labels?: TuiLabels,
  locale: Locale = 'en-US',
): ResolvedTuiLabels {
  return resolveLocale<ResolvedTuiLabels>(LOCALES, locale, labels);
}
