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
import {
  ConsoleCommandError,
  formatConsoleMessage,
  type ConsoleResultNotice,
} from './command-messages.js';
import {
  isRemoteResourceSource,
  RESOURCE_COMMAND_SPEC,
} from './resource-command-spec.js';
import { consoleCommands, createCommandHelp } from './command-catalog.js';
import { formatListResult } from './list-results.js';
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
): Promise<{
  content: CommandConsoleRecordContent;
  telemetry?: Record<string, unknown>;
  resultNotice?: ConsoleResultNotice;
}> {
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
    return { content: createCommandHelp(locale, commandOptions) };
  if (
    spec === RESOURCE_COMMAND_SPEC &&
    commandOptions?.onOpenLocalResourceImport
  ) {
    const { path, temp_file_id, ...flags } = values;
    if (!temp_file_id && !isRemoteResourceSource(path, flags.add_type)) {
      if (Object.keys(flags).length)
        throw new ConsoleCommandError('localResourceImportFlags');
      options.signal?.throwIfAborted();
      commandOptions.onOpenLocalResourceImport();
      return {
        content: {
          variant: 'text',
          text: formatConsoleMessage(
            { code: 'localResourceImportOpened' },
            locale,
          ),
        },
      };
    }
  }
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
  let data: unknown;
  let telemetry: Record<string, unknown> | undefined;
  if (spec.execute) {
    data = await spec.execute(values, execution.request, execution);
  } else {
    const request = spec.request!(values);
    if (spec.name.startsWith('session ') && values.telemetry) {
      options.signal?.throwIfAborted();
      ({ result: data, telemetry } = await client.requestEnvelope({
        ...request,
        options,
      }));
    } else {
      data = await execution.request(request);
    }
  }
  onTasks?.(readTaskReferences(spec.name, data));
  const listResult = formatListResult(spec.name, data);
  return {
    ...(listResult ?? { content: formatResult(spec.name, data, values) }),
    telemetry,
  };
}
