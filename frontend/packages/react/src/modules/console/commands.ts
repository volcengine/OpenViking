import { readTaskReferences } from '../../shared/task-tracking/read-references.js';
import type { TaskReference } from '../../shared/task-tracking/types.js';
import type {
  OvClient,
  OvRequestOptions,
  OvRequestSpec,
} from '../../client/types.js';
import type { CommandConsoleRecordContent } from '../../components/command-console/index.js';
import type { Locale } from '../../shared/locale.js';
import { COMMAND_SPECS } from './command-specs.js';
import { parseArguments, tokenize } from './command-parser.js';
import { ConsoleCommandError } from './command-messages.js';
import { consoleCommands, createCommandHelp } from './command-catalog.js';
import { formatResult } from './results.js';
import { browserCommandFiles, type CommandExecution } from './command-files.js';
import type { ConsoleCommandOptions } from './types.js';

const commandSpecsBySpecificity = [...COMMAND_SPECS].sort(
  (a, b) => b.name.length - a.name.length,
);

export function resolveCommandSpec(tokens: ReturnType<typeof tokenize>) {
  return commandSpecsBySpecificity.find((spec) =>
    spec.name.split(' ').every((part, index) => tokens[index]?.value === part),
  );
}

export async function executeCommand(
  input: string,
  client: OvClient,
  options: OvRequestOptions,
  locale: Locale,
  onTasks?: (tasks: readonly TaskReference[]) => void,
  commandOptions?: ConsoleCommandOptions,
): Promise<CommandConsoleRecordContent> {
  const tokens = tokenize(input);
  const spec = resolveCommandSpec(tokens);
  if (!spec) throw new ConsoleCommandError('unknownCommand');
  if (spec.admin && !commandOptions?.allowAdminCommands)
    throw new ConsoleCommandError('unavailableCommand', spec.name);
  let values;
  try {
    values = parseArguments(
      tokens.slice(spec.name.split(' ').length),
      spec.positional ?? [],
      spec.flags,
    );
  } catch (error) {
    const usage = consoleCommands(locale, commandOptions).find(
      (item) => item.name === spec.name,
    )!.usage;
    if (error instanceof ConsoleCommandError)
      throw new ConsoleCommandError(error.code, error.value, usage);
    throw error;
  }
  if (!spec.request && !spec.execute)
    return createCommandHelp(locale, commandOptions);
  const execution: CommandExecution = {
    signal: options.signal,
    files: commandOptions?.files ?? browserCommandFiles,
    choosePackFile: commandOptions?.choosePackFile,
    onPackImported: commandOptions?.onPackImported,
    request: <T>(spec: OvRequestSpec) => {
      options.signal?.throwIfAborted();
      return client.request<T>({ ...spec, options });
    },
  };
  const data = spec.execute
    ? await spec.execute(values, execution.request, execution)
    : await execution.request(spec.request!(values));
  onTasks?.(readTaskReferences(spec.name, data));
  return formatResult(spec.name, data, values);
}
