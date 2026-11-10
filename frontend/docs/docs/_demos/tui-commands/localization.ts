import type { TuiCommandDefinition } from '@openviking/react/components/tui';
import { COMMAND_COPY_ZH } from './copy-zh';

export function localizeCommandText(
  text: string,
  locale: 'en-US' | 'zh-CN',
): string {
  return locale === 'zh-CN' ? (COMMAND_COPY_ZH[text] ?? text) : text;
}

export function localizeCommand(
  command: TuiCommandDefinition,
  locale: 'en-US' | 'zh-CN',
): TuiCommandDefinition {
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
