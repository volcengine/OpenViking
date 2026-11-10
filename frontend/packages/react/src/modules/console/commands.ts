import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type {
  TuiCommandDefinition,
  TuiRecordContent,
} from '../../components/tui/index.js';
import type { Locale } from '../../shared/locale.js';
import { COMMAND_SPECS } from './command-specs.js';
import { parseArguments, tokenize } from './command-parser.js';
import { ConsoleCommandError } from './command-messages.js';
import { resolveConsoleLocale } from './labels.js';
import { formatResult } from './results.js';

export function consoleCommands(
  locale: Locale,
): readonly TuiCommandDefinition[] {
  const text = resolveConsoleLocale(locale);
  return COMMAND_SPECS.map((spec) => ({
    name: spec.name,
    inputText: `ov ${spec.name}`,
    description: text.commands[spec.name],
    usage: [
      'ov',
      spec.name,
      ...(spec.positional ?? []).map((arg) =>
        arg.optional ? `[${arg.name}]` : `<${arg.name}>`,
      ),
      ...(spec.flags ?? []).map(
        (flag) =>
          `[--${flag.name}${flag.kind === 'boolean' ? '' : ` <${flag.name}>`}]`,
      ),
    ].join(' '),
    params: spec.positional?.map((arg) => ({
      label: arg.name,
      description: arg.kind === 'uri' ? 'viking://…' : arg.name,
      required: !arg.optional,
    })),
    options: spec.flags?.map((flag) => ({
      token: `--${flag.name}`,
      description: flag.choices?.join(' | ') ?? flag.name,
    })),
    skeleton: spec.skeleton,
  }));
}

export function createCommandHelp(locale: Locale): TuiRecordContent {
  return {
    variant: 'command-help',
    sections: [
      {
        key: 'commands',
        label: resolveConsoleLocale(locale).availableCommands,
        items: consoleCommands(locale).map((item) => ({
          key: item.name,
          name: item.usage,
          description: item.description,
        })),
      },
    ],
  };
}

export async function executeCommand(
  input: string,
  client: OvClient,
  options: OvRequestOptions,
  locale: Locale,
): Promise<TuiRecordContent> {
  const tokens = tokenize(input);
  const spec = [...COMMAND_SPECS]
    .sort((a, b) => b.name.length - a.name.length)
    .find((item) =>
      item.name
        .split(' ')
        .every((part, index) => tokens[index]?.value === part),
    );
  if (!spec) throw new ConsoleCommandError('unknownCommand');
  let values;
  try {
    values = parseArguments(
      tokens.slice(spec.name.split(' ').length),
      spec.positional ?? [],
      spec.flags,
    );
  } catch (error) {
    const usage = consoleCommands(locale).find(
      (item) => item.name === spec.name,
    )!.usage;
    if (error instanceof ConsoleCommandError)
      throw new ConsoleCommandError(error.code, error.value, usage);
    throw error;
  }
  if (!spec.request) return createCommandHelp(locale);
  const data = await client.request<unknown>({
    ...spec.request(values),
    options,
  });
  return formatResult(spec.name, data, values);
}
