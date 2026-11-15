import { isOvClientError } from '../../client/errors.js';
import type { CommandConsoleRecord } from '../../components/command-console/index.js';
import type { Locale } from '../../shared/locale.js';
import { resolveConsoleLocale } from './labels.js';

export interface ConsoleMessage {
  code: keyof ReturnType<typeof resolveConsoleLocale>['messages'];
  value?: string;
  usage?: string;
}

/** Internal metadata is formatted by the Hook and never exposed in its records. */
export type ConsoleRecord = CommandConsoleRecord & {
  localMessage?: ConsoleMessage;
  taskKeys?: readonly string[];
};

export function formatConsoleMessage(message: ConsoleMessage, locale: Locale) {
  const format = resolveConsoleLocale(locale).messages[message.code];
  const text = format(message.value ?? '');
  return message.usage ? `${text}\n${message.usage}` : text;
}

export class ConsoleCommandError extends Error implements ConsoleMessage {
  constructor(
    readonly code: ConsoleMessage['code'],
    readonly value?: string,
    readonly usage?: string,
    locale: Locale = 'en-US',
  ) {
    super(formatConsoleMessage({ code, value, usage }, locale));
  }
}

export function commandErrorText(error: unknown) {
  return isOvClientError(error)
    ? `${error.code}: ${error.message}`
    : error instanceof Error
      ? error.message
      : String(error);
}
