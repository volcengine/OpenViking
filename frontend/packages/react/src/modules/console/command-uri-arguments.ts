import type { CommandConsoleCommandDefinition } from '../../components/command-console/index.js';
import { ConsoleCommandError } from './command-messages.js';
import { parseArguments, type Argument } from './command-parser.js';
import { tokenizeArguments } from './command-tokenizer.js';

export function resolveCommandUriArguments(
  text: string,
  positional: readonly Argument[] = [],
  flags: readonly Argument[] = [],
): ReturnType<
  NonNullable<CommandConsoleCommandDefinition['resolveUriArguments']>
> {
  const ranges: { start: number; end: number; uri: string }[] = [];
  try {
    parseArguments(
      tokenizeArguments(text),
      positional,
      flags,
      (argument, value, start, end) => {
        const isList =
          argument.kind === 'uri-list' || argument.kind === 'uri-or-list';
        if (!isList && argument.kind !== 'uri' && argument.name !== 'uri')
          return;

        const quote = text[start];
        if ((quote === '"' || quote === "'") && text[end - 1] === quote) {
          start++;
          end--;
        }
        const rawParts = isList
          ? text.slice(start, end).split(',')
          : [text.slice(start, end)];
        const values = isList ? value.split(',') : [value];
        for (const [index, raw] of rawParts.entries()) {
          const uri = values[index]?.trim();
          if (uri?.startsWith('viking://')) {
            ranges.push({
              start: start + raw.length - raw.trimStart().length,
              end: start + raw.trimEnd().length,
              uri,
            });
          }
          start += raw.length + 1;
        }
      },
    );
  } catch (error) {
    // Drafts may be incomplete or invalid. Keep ranges for arguments already recognized.
    if (!(error instanceof ConsoleCommandError)) throw error;
  }
  return ranges;
}
