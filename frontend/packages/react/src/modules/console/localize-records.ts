import type { Locale } from '../../shared/locale.js';
import { createCommandHelp } from './commands.js';
import {
  formatConsoleMessage,
  type ConsoleRecord,
} from './command-messages.js';

/** Format library notices without changing server output or execution state. */
export function localizeConsoleRecords(
  records: readonly ConsoleRecord[],
  locale: Locale,
) {
  return records.map(({ localMessage, ...record }) => {
    if (localMessage) {
      return {
        ...record,
        content: {
          variant: 'text' as const,
          text: formatConsoleMessage(localMessage, locale),
        },
      };
    }
    if (record.content?.variant === 'command-help') {
      return { ...record, content: createCommandHelp(locale) };
    }
    return record;
  });
}
