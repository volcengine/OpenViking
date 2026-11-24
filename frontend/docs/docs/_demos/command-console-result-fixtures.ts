import type { CommandConsoleRecord } from '@openviking/react/components/command-console';
import {
  createCommandConsoleCommands,
  createCommandConsoleCommandGroups,
} from '#command-console-commands';

/** Fixed, public demo data; no command is sent to a service. */
export function createCommandConsoleCompileRecord(): CommandConsoleRecord {
  const source = 'viking://resources/acme';
  const compileName = 'demo-wiki';
  return {
    id: 'compile',
    command: `ov compile --compile-name ${compileName}`,
    status: 'success',
    createdAt: '10:28',
    content: {
      variant: 'write-result',
      lines: [
        'Task id: cmp_demo',
        'Task type: compile',
        'Status: pending',
        'Created at: 1794191280',
        'Updated at: 1794191281',
        `Resource id: ${source}`,
        `Request: ${JSON.stringify({ compile_name: compileName })}`,
        'Stage: queued',
        'Created at iso: 2026-11-09T02:28:00+00:00',
        'Updated at iso: 2026-11-09T02:28:01+00:00',
      ],
    },
  };
}

export function createCommandConsoleCompileCreateRecord(): CommandConsoleRecord {
  return {
    id: 'compile-create',
    command: 'ov compile create --compile-name demo-wiki',
    status: 'success',
    content: {
      variant: 'write-result',
      lines: [
        'Configuration: demo-wiki',
        'Status: saved',
        'Compilation: not started',
      ],
    },
  };
}

export function createCommandConsoleHelp(
  isChinese: boolean,
): CommandConsoleRecord {
  const locale = isChinese ? 'zh-CN' : 'en-US';
  const commands = createCommandConsoleCommands(locale);
  return {
    id: 'help',
    command: 'ov help',
    status: 'success',
    content: {
      variant: 'command-help',
      sections: createCommandConsoleCommandGroups(locale).flatMap((group) => {
        const items = commands
          .filter((command) => command.group === group.key)
          .map((command) => ({
            key: command.name,
            name: command.inputText ?? command.name,
            description: command.summary ?? command.description,
          }));
        return items.length
          ? [{ key: group.key, label: group.label, items }]
          : [];
      }),
    },
  };
}
