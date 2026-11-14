import { OvClientError } from '../../../client/errors.js';
import { resolveLocale, type Locale } from '../../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

/** Only library-generated messages are translated; backend content stays intact. */
export function localizeBotError(error: Error | undefined, locale: Locale) {
  if (!(error instanceof OvClientError) || !(error.code in enUS)) return error;
  return new OvClientError({
    code: error.code,
    message: resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale)[
      error.code as keyof typeof enUS
    ],
    cause: error,
  });
}

export const asBotError = (value: unknown) =>
  value instanceof Error ? value : new Error(String(value));
