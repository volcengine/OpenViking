import type { VikingBotInteractionLabels } from './types.js';
import { resolveLocale, type Locale } from '../../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = { 'en-US': enUS, 'zh-CN': zhCN };

/** 内部完整文案类型，从公开类型派生，保证宿主可覆盖每一处文案。 */
export type InteractionLabels = Required<
  Omit<VikingBotInteractionLabels, 'progress'>
> & {
  progress: (index: number, total: number) => string;
};

export const DEFAULT_INTERACTION_LABELS: InteractionLabels = enUS;

export function resolveInteractionLabels(
  locale: Locale,
  labels?: VikingBotInteractionLabels,
): InteractionLabels {
  return resolveLocale<InteractionLabels>(LOCALES, locale, labels);
}
