import type { FileTreeLabels } from './types.js';
import type { ResolvedLabels } from './file-tree-row-context.js';
import {
  mergeLabels,
  resolveLocale,
  type Locale,
} from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = { 'en-US': enUS, 'zh-CN': zhCN };

export function resolveFileTreeLabels(
  locale: Locale = 'en-US',
  labels?: FileTreeLabels,
): ResolvedLabels {
  const defaults = resolveLocale(LOCALES, locale);
  return {
    ...mergeLabels<ResolvedLabels>(defaults, labels),
    info: {
      description: mergeLabels(
        defaults.info.description,
        labels?.info?.description,
      ),
      triggerBehavior: mergeLabels(
        defaults.info.triggerBehavior,
        labels?.info?.triggerBehavior,
      ),
      field: mergeLabels(defaults.info.field, labels?.info?.field),
    },
  };
}
