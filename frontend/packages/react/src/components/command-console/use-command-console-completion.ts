import { groupCommandConsoleCommands } from './command-definitions.js';
import type {
  CommandConsoleCommandDefinition,
  CommandConsoleCommandGroup,
} from './command-catalog.js';
import { matchCommandConsoleCommandInput } from './command-input.js';
import type { CommandConsoleCommandInputMatch } from './command-input.js';
import { getCommandConsoleCommandQuery } from './command-completion.js';
import type { CommandConsoleCommandQuery } from './command-completion.js';
import type { CommandConsoleCommandSection } from './command-definitions.js';

export interface CommandConsoleCompletion {
  query: CommandConsoleCommandQuery;
  suggestions: readonly CommandConsoleCommandSection[];
  options: readonly CommandConsoleCommandDefinition[];
  activeInput: CommandConsoleCommandInputMatch | undefined;
  showSuggestions: boolean;
  enabled: boolean;
}

/** 由受控输入和宿主目录推导补全，无取数、执行或状态镜像。 */
export function useCommandConsoleCompletion(
  value: string,
  enabled: boolean,
  commands: readonly CommandConsoleCommandDefinition[],
  groups: readonly CommandConsoleCommandGroup[] = [],
  paletteScope: string | null = null,
): CommandConsoleCompletion {
  const query = getCommandConsoleCommandQuery(value, commands);
  const suggestions = groupCommandConsoleCommands(
    enabled ? query.matches : [],
    groups,
  );
  return {
    query,
    suggestions,
    options: suggestions.flatMap((section) => section.commands),
    enabled,
    activeInput:
      query.scope === '/'
        ? undefined
        : matchCommandConsoleCommandInput(value, commands),
    showSuggestions:
      enabled &&
      (query.automatic ||
        (query.scope !== null && paletteScope === query.scope)),
  };
}
