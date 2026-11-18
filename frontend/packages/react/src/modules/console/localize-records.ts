import type { Locale } from '../../shared/locale.js';
import { createCommandHelp } from './command-catalog.js';
import type { ConsoleCommandOptions } from './types.js';
import {
  formatConsoleMessage,
  type ConsoleRecord,
} from './command-messages.js';

/** Format library notices without changing server output or execution state. */
export function localizeConsoleRecords(
  records: readonly ConsoleRecord[],
  locale: Locale,
  commandOptions?: ConsoleCommandOptions,
) {
  return records.map(({ localMessage, taskKeys: _taskKeys, ...record }) => {
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
      return { ...record, content: createCommandHelp(locale, commandOptions) };
    }
    return record;
  });
}
