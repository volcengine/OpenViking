import type { CommandConsoleCommandDefinition } from '@openviking/react/components/command-console';
import { COMMAND_COPY_ZH } from './copy-zh';

export function localizeCommandText(
  text: string,
  locale: 'en-US' | 'zh-CN',
): string {
  return locale === 'zh-CN' ? (COMMAND_COPY_ZH[text] ?? text) : text;
}

export function localizeCommand(
  command: CommandConsoleCommandDefinition,
  locale: 'en-US' | 'zh-CN',
): CommandConsoleCommandDefinition {
  if (locale === 'en-US') return command;
  return {
    ...command,
    description: localizeCommandText(command.description, locale),
    summary: command.summary
      ? localizeCommandText(command.summary, locale)
      : undefined,
    params: command.params?.map((param) => ({
      ...param,
      description: localizeCommandText(param.description, locale),
    })),
    options: command.options?.map((option) => ({
      ...option,
      description: localizeCommandText(option.description, locale),
    })),
  };
}
