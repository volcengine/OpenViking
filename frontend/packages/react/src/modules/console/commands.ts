import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type {
  TuiCommandDefinition,
  TuiRecordContent,
} from '../../components/tui/index.js';
import { COMMAND_SPECS } from './command-specs.js';
import { parseArguments, tokenize } from './command-parser.js';
import { formatResult } from './results.js';

export function consoleCommands(
  locale: 'en' | 'zh',
): readonly TuiCommandDefinition[] {
  return COMMAND_SPECS.map((spec) => ({
    name: spec.name,
    inputText: `ov ${spec.name}`,
    description: spec.description[locale === 'zh' ? 1 : 0],
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

export async function executeCommand(
  input: string,
  client: OvClient,
  options: OvRequestOptions,
  locale: 'en' | 'zh',
): Promise<TuiRecordContent> {
  const tokens = tokenize(input);
  const spec = [...COMMAND_SPECS]
    .sort((a, b) => b.name.length - a.name.length)
    .find((item) =>
      item.name
        .split(' ')
        .every((part, index) => tokens[index]?.value === part),
    );
  if (!spec)
    throw new Error(
      locale === 'zh'
        ? '未知命令。输入 ov help 查看可用命令。'
        : 'Unknown command. Enter ov help for available commands.',
    );
  let values;
  try {
    values = parseArguments(
      tokens.slice(spec.name.split(' ').length),
      spec.positional ?? [],
      spec.flags,
    );
  } catch (error) {
    throw new Error(
      `${error instanceof Error ? error.message : String(error)}\n${consoleCommands(locale).find((item) => item.name === spec.name)!.usage}`,
      { cause: error },
    );
  }
  if (!spec.request)
    return {
      variant: 'command-help',
      sections: [
        {
          key: 'commands',
          label: locale === 'zh' ? '可用命令' : 'Available commands',
          items: consoleCommands(locale).map((item) => ({
            key: item.name,
            name: item.usage,
            description: item.description,
          })),
        },
      ],
    };
  const data = await client.request<unknown>({
    ...spec.request(values),
    options,
  });
  return formatResult(spec.name, data, values);
}
