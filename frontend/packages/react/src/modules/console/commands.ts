import { readTaskReferences } from '../../shared/task-tracking/read-references.js';
import { downloadBlob } from '../../shared/download-blob.js';
import type { TaskReference } from '../../shared/task-tracking/types.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type {
  CommandConsoleCommandDefinition,
  CommandConsoleCommandGroup,
  CommandConsoleQuickCommand,
  CommandConsoleRecordContent,
} from '../../components/command-console/index.js';
import type { Locale } from '../../shared/locale.js';
import { COMMAND_GROUPS } from './command-groups.js';
import { COMMAND_SPECS, type CommandSpec } from './command-specs.js';
import { parseArguments, tokenize, type Argument } from './command-parser.js';
import { ConsoleCommandError } from './command-messages.js';
import { resolveConsoleLocale } from './labels.js';
import { formatResult } from './results.js';
import type { ConsoleCommandGuide } from './types.js';
import { parameterExamples } from './parameter-examples.js';

export function consoleCommandGroups(
  locale: Locale,
): readonly CommandConsoleCommandGroup[] {
  const labels = resolveConsoleLocale(locale).commandGroups;
  return (Object.keys(COMMAND_GROUPS) as (keyof typeof COMMAND_GROUPS)[]).map(
    (key) => ({ key, label: labels[key] }),
  );
}

const DEFAULT_QUICK_COMMAND_NAMES = [
  'add-resource',
  'add-skill',
  'add-memory',
  'find',
  'compile',
] as const;

export function consoleQuickCommands(
  locale: Locale,
): readonly CommandConsoleQuickCommand[] {
  const guides = resolveConsoleLocale(locale).commandGuides;
  return DEFAULT_QUICK_COMMAND_NAMES.map((name) => ({
    name,
    value: name === 'compile' ? 'ov compile ' : guides[name].examples[0],
  }));
}

export function consoleCommands(
  locale: Locale,
): readonly CommandConsoleCommandDefinition[] {
  const text = resolveConsoleLocale(locale);
  const guides: Partial<Record<CommandSpec['name'], ConsoleCommandGuide>> =
    text.commandGuides;
  const descriptions: Record<string, string> = text.parameters;
  const catalog = new Map(
    COMMAND_SPECS.map((spec) => {
      const guide = guides[spec.name];
      const positional = (
        spec.preferredPositional ??
        spec.positional ??
        []
      ).filter((arg) => !arg.hidden);
      const flags = (spec.preferredFlags ?? spec.flags ?? []).filter(
        (arg) => !arg.hidden,
      );
      return [
        spec.name,
        {
          name: spec.name,
          inputText: `ov ${spec.name}`,
          description: guide?.description ?? text.commands[spec.name],
          summary: text.commands[spec.name],
          examples: guide?.examples ?? parameterExamples[spec.name],
          quickStart: guide?.quickStart,
          usage: [
            'ov',
            spec.name,
            ...positional.map((arg) =>
              arg.optional
                ? `[${arg.placeholder ?? arg.name}]`
                : `<${arg.placeholder ?? arg.name}>`,
            ),
            ...flags.map((flag) => {
              const token = `--${flag.name}${parameterValue(flag)}`;
              return flag.required ? token : `[${token}]`;
            }),
          ].join(' '),
          params: positional.map((arg) => ({
            label: arg.name,
            description:
              descriptions[arg.descriptionKey ?? arg.name] ??
              (arg.kind === 'uri' ? 'viking://…' : arg.name),
            required: !arg.optional,
            defaultValue: formatDefault(arg.defaultValue),
          })),
          options: flags.map((flag) => ({
            token: `${flag.alias ? `${flag.alias}, ` : ''}--${flag.name}${parameterValue(flag)}`,
            required: flag.required,
            defaultValue: formatDefault(flag.helpDefault ?? flag.defaultValue),
            description:
              descriptions[flag.descriptionKey ?? flag.name] ??
              flag.choices?.join(' | ') ??
              flag.name,
          })),
          skeleton: spec.skeleton,
        },
      ] as const;
    }),
  );
  return Object.entries(COMMAND_GROUPS).flatMap(([group, names]) =>
    names.flatMap((name) => {
      const command = catalog.get(name);
      return command ? [{ ...command, group }] : [];
    }),
  );
}

function parameterValue(flag: Argument) {
  if (flag.kind === 'boolean') return '';
  const value = `<${flag.placeholder ?? flag.name}>`;
  return flag.kind === 'boolean-or-json' ? ` [${value}]` : ` ${value}`;
}

function formatDefault(value: Argument['defaultValue']) {
  return value === undefined ? undefined : String(value);
}

export function createCommandHelp(locale: Locale): CommandConsoleRecordContent {
  const commands = consoleCommands(locale);
  return {
    variant: 'command-help',
    sections: consoleCommandGroups(locale).map(({ key, label }) => ({
      key,
      label,
      items: commands
        .filter((command) => command.group === key)
        .map((item) => ({
          key: item.name,
          name: item.usage,
          description: item.description,
        })),
    })),
  };
}

export async function executeCommand(
  input: string,
  client: OvClient,
  options: OvRequestOptions,
  locale: Locale,
  onTasks?: (tasks: readonly TaskReference[]) => void,
): Promise<CommandConsoleRecordContent> {
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
  if (!spec.request && !spec.execute) return createCommandHelp(locale);
  const data = spec.execute
    ? await spec.execute(values, (request) => {
        options.signal?.throwIfAborted();
        return client.request({ ...request, options });
      })
    : await client.request({ ...spec.request!(values), options });
  if (spec.downloadFilename) {
    options.signal?.throwIfAborted();
    const filename = spec.downloadFilename(values);
    downloadBlob(data as Blob, filename);
    return {
      variant: 'write-result',
      lines: [resolveConsoleLocale(locale).downloadedFile(filename)],
    };
  }
  onTasks?.(readTaskReferences(spec.name, data));
  return formatResult(spec.name, data, values);
}
