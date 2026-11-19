import { resolveLocale } from '../../shared/locale.js';
import type { AgentConnectLocale } from './types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

export function resolveAgentConnectLabels(locale: AgentConnectLocale) {
  return resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
}
