import type { TuiRecord } from '@openviking/react/components/tui';
import { createTuiCommands, createTuiCommandGroups } from '#tui-commands';

/** Fixed, public demo data; no command is sent to a service. */
export function createTuiCompileRecord(): TuiRecord {
  const source = 'viking://resources/acme';
  const target = 'viking://resources/acme-wiki';
  const skill = 'viking://system/compile/example-skills/llm-wiki';
  return {
    id: 'compile',
    command: `ov compile --from ${source} --to ${target} --skill ${skill}/SKILL.md`,
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
        `Request: ${JSON.stringify({ from: [source], to: target, skill, compile: { mode: 'incremental', trigger: 'manual' } })}`,
        'Stage: queued',
        'Created at iso: 2026-11-09T02:28:00+00:00',
        'Updated at iso: 2026-11-09T02:28:01+00:00',
      ],
    },
  };
}

export function createTuiHelp(isChinese: boolean): TuiRecord {
  const locale = isChinese ? 'zh' : 'en';
  const commands = createTuiCommands(locale);
  return {
    id: 'help',
    command: 'ov help',
    status: 'success',
    content: {
      variant: 'command-help',
      sections: createTuiCommandGroups(locale).flatMap((group) => {
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
