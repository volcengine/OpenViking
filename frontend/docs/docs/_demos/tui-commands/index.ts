import type {
  TuiCommandDefinition,
  TuiCommandGroup,
} from '@openviking/react/components/tui';
import { CORE_COMMANDS } from './core';
import { createAdditionalTuiCommands } from './additional';
import { FILESYSTEM_COMMANDS } from './filesystem';
import { SESSION_COMMANDS } from './session';
import { localizeCommand, localizeCommandText } from './localization';

const GROUPS: readonly TuiCommandGroup[] = [
  { key: 'core', label: 'Core workflow' },
  { key: 'filesystem', label: 'Filesystem' },
  { key: 'search', label: 'Search & context' },
  { key: 'status', label: 'Config & status' },
  { key: 'session', label: 'Import, export & sessions' },
  { key: 'interactive', label: 'Interactive & admin' },
];
export function createTuiCommands(
  locale: 'en' | 'zh',
): readonly TuiCommandDefinition[] {
  return [
    ...CORE_COMMANDS,
    ...FILESYSTEM_COMMANDS,
    ...SESSION_COMMANDS,
    ...createAdditionalTuiCommands(locale),
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
export function createTuiCommandGroups(
  locale: 'en' | 'zh',
): readonly TuiCommandGroup[] {
  return GROUPS.map((group) => ({
    ...group,
    label: localizeCommandText(group.label, locale),
  }));
}
