import { groupTuiCommands } from './command-definitions.js';
import type {
  TuiCommandDefinition,
  TuiCommandGroup,
} from './command-catalog.js';
import { matchTuiCommandInput } from './command-input.js';
import type { TuiCommandInputMatch } from './command-input.js';
import { getTuiCommandQuery } from './command-completion.js';
import type { TuiCommandQuery } from './command-completion.js';
import type { TuiCommandSection } from './command-definitions.js';

export interface TuiCompletion {
  query: TuiCommandQuery;
  suggestions: readonly TuiCommandSection[];
  options: readonly TuiCommandDefinition[];
  activeInput: TuiCommandInputMatch | undefined;
  showSuggestions: boolean;
  enabled: boolean;
}

/** 由受控输入和宿主目录推导补全，无取数、执行或状态镜像。 */
export function useTuiCompletion(
  value: string,
  enabled: boolean,
  commands: readonly TuiCommandDefinition[],
  groups: readonly TuiCommandGroup[] = [],
  paletteScope: string | null = null,
): TuiCompletion {
  const query = getTuiCommandQuery(value, commands);
  const suggestions = groupTuiCommands(enabled ? query.matches : [], groups);
  return {
    query,
    suggestions,
    options: suggestions.flatMap((section) => section.commands),
    enabled,
    activeInput:
      query.scope === '/' ? undefined : matchTuiCommandInput(value, commands),
    showSuggestions:
      enabled &&
      (query.automatic ||
        (query.scope !== null && paletteScope === query.scope)),
  };
}
