import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type {
  TuiCommandDefinition,
  TuiCommandGroup,
  TuiQuickCommand,
  TuiRecordContent,
} from '../../components/tui/index.js';
import type { Locale } from '../../shared/locale.js';
import { CORE_COMMAND_GUIDES } from './core-command-specs.js';
import { COMMAND_SPECS, type CommandSpec } from './command-specs.js';
import { parseArguments, tokenize } from './command-parser.js';
import { ConsoleCommandError } from './command-messages.js';
import { resolveConsoleLocale } from './labels.js';
import { formatResult } from './results.js';

// Volcengine's command palette order, restricted to commands this Console can execute.
const COMMAND_GROUPS = {
  core: [
    'add-resource',
    'add-skill',
    'compile',
    'find',
    'read',
    'write',
    'add-memory',
    'compile create',
  ],
  filesystem: ['ls', 'tree', 'mkdir', 'rm', 'mv', 'stat', 'cp'],
  search: ['find', 'search', 'grep', 'glob', 'abstract', 'overview', 'read'],
  status: [
    'help',
    'health',
    'status',
    'task list',
    'task status',
    'task cancel',
    'clear',
  ],
} satisfies Record<string, readonly CommandSpec['name'][]>;

export function consoleCommandGroups(
  locale: Locale,
): readonly TuiCommandGroup[] {
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
): readonly TuiQuickCommand[] {
  const guides = CORE_COMMAND_GUIDES[locale];
  return DEFAULT_QUICK_COMMAND_NAMES.map((name) => ({
    name,
    value:
      (name === 'compile' ? undefined : guides[name].examples[0]) ??
      `ov ${name} `,
  }));
}

export function consoleCommands(
  locale: Locale,
): readonly TuiCommandDefinition[] {
  const text = resolveConsoleLocale(locale);
  const guides = CORE_COMMAND_GUIDES[locale];
  const descriptions: Record<string, string> = text.parameters;
  const catalog = new Map(
    COMMAND_SPECS.map((spec) => {
      const guide = guides[spec.name as keyof typeof guides];
      return [
        spec.name,
        {
          name: spec.name,
          inputText: `ov ${spec.name}`,
          description: guide?.description ?? text.commands[spec.name],
          summary: text.commands[spec.name],
          examples: guide?.examples,
          usage: [
            'ov',
            spec.name,
            ...(spec.positional ?? []).map((arg) =>
              arg.optional ? `[${arg.name}]` : `<${arg.name}>`,
            ),
            ...(spec.flags ?? []).map((flag) =>
              flag.required
                ? `--${flag.name} <${flag.name}>`
                : `[--${flag.name}${flag.kind === 'boolean' ? '' : ` <${flag.name}>`}]`,
            ),
          ].join(' '),
          params: spec.positional?.map((arg) => ({
            label: arg.name,
            description:
              descriptions[arg.name] ??
              (arg.kind === 'uri' ? 'viking://…' : arg.name),
            required: !arg.optional,
          })),
          options: spec.flags?.map((flag) => ({
            token: flag.alias
              ? `${flag.alias}, --${flag.name}`
              : `--${flag.name}`,
            required: flag.required,
            defaultValue: flag.defaultValue,
            description:
              descriptions[flag.name] ?? flag.choices?.join(' | ') ?? flag.name,
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

export function createCommandHelp(locale: Locale): TuiRecordContent {
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
  if (!spec.request && !spec.execute) return createCommandHelp(locale);
  const data = spec.execute
    ? await spec.execute(values, (request) => {
        options.signal?.throwIfAborted();
        return client.request({ ...request, options });
      })
    : await client.request({ ...spec.request!(values), options });
  return formatResult(spec.name, data, values);
}
