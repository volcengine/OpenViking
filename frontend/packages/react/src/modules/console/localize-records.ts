import type { Locale } from '../../shared/locale.js';
import { createCommandHelp } from './command-catalog.js';
import { resolveConsoleLocale } from './labels.js';
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
  const { telemetryResult } = resolveConsoleLocale(locale);
  return records.map(
    ({ localMessage, taskKeys: _taskKeys, telemetry, ...record }) => {
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
        return {
          ...record,
          content: createCommandHelp(locale, commandOptions),
        };
      }
      if (telemetry && record.content?.variant === 'write-result') {
        return {
          ...record,
          content: {
            ...record.content,
            lines: [
              ...record.content.lines,
              telemetryResult(JSON.stringify(telemetry, null, 2)),
            ],
          },
        };
      }
      return record;
    },
  );
}
