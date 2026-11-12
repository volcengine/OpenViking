import type {
  CommandConsoleCommandDefinition,
  CommandConsoleCommandGroup,
} from '@openviking/react/components/command-console';
import { CORE_COMMANDS } from './core';
import { COMPILE_COMMANDS } from './compile';
export { createCommandConsoleQuickCommands } from './quick-commands';
import { createAdditionalCommandConsoleCommands } from './additional';
import { FILESYSTEM_COMMANDS } from './filesystem';
import { SESSION_COMMANDS } from './session';
import { localizeCommand, localizeCommandText } from './localization';

const GROUPS: readonly CommandConsoleCommandGroup[] = [
  { key: 'core', label: 'Core workflow' },
  { key: 'filesystem', label: 'Filesystem' },
  { key: 'search', label: 'Search & context' },
  { key: 'status', label: 'Config & status' },
  { key: 'session', label: 'Import, export & sessions' },
  { key: 'interactive', label: 'Interactive & admin' },
];
export function createCommandConsoleCommands(
  locale: 'en-US' | 'zh-CN',
): readonly CommandConsoleCommandDefinition[] {
  return [
    ...CORE_COMMANDS,
    ...COMPILE_COMMANDS,
    ...FILESYSTEM_COMMANDS,
    ...SESSION_COMMANDS,
    ...createAdditionalCommandConsoleCommands(locale),
  ].map((command) => {
    const localized = localizeCommand(command, locale);
    return {
      ...localized,
      inputText: `ov ${command.name}`,
      usage: command.usage.replace(/^\//, 'ov '),
      params: localized.params?.map((param) => ({
        ...param,
        required: param.required ?? param.label.startsWith('<'),
      })),
      examples: command.examples?.map((example) => {
        const value = typeof example === 'string' ? example : example.value;
        return { value: value.replace(/^\//, 'ov ') };
      }),
    };
  });
}
export function createCommandConsoleCommandGroups(
  locale: 'en-US' | 'zh-CN',
): readonly CommandConsoleCommandGroup[] {
  return GROUPS.map((group) => ({
    ...group,
    label: localizeCommandText(group.label, locale),
  }));
}
