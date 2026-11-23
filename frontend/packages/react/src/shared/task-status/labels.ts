import en from './locales/en-US.js';
import zh from './locales/zh-CN.js';
import type { Locale } from '../locale.js';
export const taskLabels = (locale: Locale) => (locale === 'zh-CN' ? zh : en);
