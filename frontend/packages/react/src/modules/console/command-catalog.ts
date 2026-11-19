import type {
  CommandConsoleCommandDefinition,
  CommandConsoleCommandGroup,
  CommandConsoleQuickCommand,
  CommandConsoleRecordContent,
} from '../../components/command-console/index.js';
import type { Locale } from '../../shared/locale.js';
import { COMMAND_SPECS, type CommandSpec } from './command-specs.js';
import type { Argument } from './command-parser.js';
import { resolveCommandUriArguments } from './command-uri-arguments.js';
import { resolveConsoleLocale } from './labels.js';
import type { ConsoleCommandGuide, ConsoleCommandOptions } from './types.js';
import { parameterExamples } from './parameter-examples.js';

// Command palette order, restricted to the commands this Console can execute.
const COMMAND_GROUPS = {
  core: [
    'add-resource',
    'add-skill',
    'compile create',
    'compile',
    'find',
    'read',
    'write',
    'add-memory',
  ],
  filesystem: ['ls', 'tree', 'mkdir', 'rm', 'mv', 'stat', 'cp'],
  search: ['find', 'search', 'grep', 'glob', 'abstract', 'overview', 'read'],
  transfer: ['get', 'import', 'export', 'backup', 'restore'],
  session: [
    'session new',
    'session list',
    'session get',
    'session get-session-context',
    'session get-session-archive',
    'session delete',
    'session add-message',
    'session add-messages',
    'session commit',
    'session config set',
  ],
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
  'compile create',
  'compile',
] as const;

export function consoleQuickCommands(
  locale: Locale,
): readonly CommandConsoleQuickCommand[] {
  const guides = resolveConsoleLocale(locale).commandGuides;
  return DEFAULT_QUICK_COMMAND_NAMES.map((name) => ({
    name,
    value: guides[name].examples[0],
  }));
}

export function consoleCommands(
  locale: Locale,
  options?: ConsoleCommandOptions,
): readonly CommandConsoleCommandDefinition[] {
  const text = resolveConsoleLocale(locale);
  const guides: Partial<Record<CommandSpec['name'], ConsoleCommandGuide>> =
    text.commandGuides;
  const descriptions: Record<string, string> = text.parameters;
  const catalog = new Map<string, CommandConsoleCommandDefinition>(
    COMMAND_SPECS.filter(
      (spec) => !spec.admin || options?.allowAdminCommands,
    ).map((spec) => {
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
          resolveUriArguments: (input) =>
            resolveCommandUriArguments(input, spec.positional, spec.flags),
        },
      ] as const;
    }),
  );
  const create = catalog.get('compile create');
  if (create) {
    create.parameterDetails = {
      usage: create.usage,
      options: create.options,
      extra: text.compileArgsHelp,
    };
  }

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

export function createCommandHelp(
  locale: Locale,
  options?: ConsoleCommandOptions,
): CommandConsoleRecordContent {
  const commands = consoleCommands(locale, options);
  return {
    variant: 'command-help',
    sections: consoleCommandGroups(locale)
      .filter(({ key }) => commands.some((command) => command.group === key))
      .map(({ key, label }) => ({
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
