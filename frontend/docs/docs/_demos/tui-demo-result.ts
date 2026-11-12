import type {
  TuiRecord,
  TuiRecordContent,
} from '@openviking/react/components/tui';
import { createTuiCommands } from '#tui-commands';
import { createTuiRecords } from '#tui-records';
import {
  createTuiCompileRecord,
  createTuiCompileCreateRecord,
  createTuiHelp,
} from './tui-result-fixtures';

/** The demo host chooses a result shape; the shared component never executes CLI commands. */
export function createTuiDemoRecord(
  command: string,
  locale: 'en-US' | 'zh-CN',
): TuiRecord {
  const chinese = locale === 'zh-CN';
  const catalogue = createTuiCommands(locale);
  const input = command
    .trim()
    .toLowerCase()
    .replace(/[ \t]+/g, ' ');
  const definition = [...catalogue]
    .sort((a, b) => b.name.length - a.name.length)
    .find((item) =>
      [
        item.inputText ?? item.name,
        ...[item.name, ...(item.aliases ?? [])].flatMap((name) => [
          name,
          `/${name}`,
          `ov ${name}`,
        ]),
      ].some(
        (prefix) =>
          input === prefix.toLowerCase() ||
          input.startsWith(`${prefix.toLowerCase()} `),
      ),
    );
  const record = { id: 'demo-result', command, status: 'success' as const };
  if (!definition)
    return {
      ...record,
      status: 'error',
      content: {
        variant: 'text',
        text: chinese
          ? '未知命令。输入 / 查看可用命令（模拟结果）。'
          : 'Unknown command. Type / to see available commands (demo result).',
      },
    };
  if (definition.name === 'help')
    return { ...createTuiHelp(chinese), ...record };
  if (definition.name === 'compile create')
    return { ...createTuiCompileCreateRecord(), ...record };
  if (definition.name === 'compile')
    return { ...createTuiCompileRecord(), ...record };
  const examples = createTuiRecords(chinese);
  const preset = (id: string) =>
    examples.find((item) => item.id === id)!.content!;
  let content: TuiRecordContent;
  if (['task list', 'session list'].includes(definition.name)) {
    content = {
      variant: 'resource-tree',
      items: [
        {
          id: 'entry',
          title: 'demo',
          detailRows: [
            {
              label: 'Id',
              value:
                definition.name === 'task list' ? 'cmp_demo' : 'session_demo',
            },
            { label: 'Status', value: 'pending' },
            { label: 'Created at', value: '2026-11-09T02:28:00+00:00' },
          ],
        },
      ],
    };
  } else if (definition.name.startsWith('task ')) {
    content = {
      variant: 'status',
      rows: [
        { label: 'Task id', value: 'cmp_demo' },
        {
          label: 'Status',
          value: definition.name === 'task cancel' ? 'cancelled' : 'pending',
        },
        {
          label: 'Request',
          value: JSON.stringify(
            {
              from: ['viking://resources/acme'],
              to: 'viking://resources/acme-wiki',
            },
            null,
            2,
          ),
          variant: 'pre',
        },
      ],
    };
  } else if (definition.skeleton === 'search') content = preset('find');
  else if (definition.skeleton === 'tree') content = preset('ls');
  else if (definition.skeleton === 'resource') content = preset('read');
  else if (definition.skeleton === 'status')
    content =
      definition.name === 'stat'
        ? preset('stat')
        : {
            variant: 'status',
            rows: [
              { label: 'Status', value: 'ok' },
              {
                label: 'Result',
                value: JSON.stringify(
                  { command: definition.name, demo: true },
                  null,
                  2,
                ),
                variant: 'pre',
              },
            ],
          };
  else if (definition.skeleton === 'write')
    content = {
      variant: 'write-result',
      lines: [
        'Status: accepted',
        'Resource id: viking://resources/acme',
        `Operation: ${definition.name}`,
      ],
    };
  else
    content = {
      variant: 'text',
      text: JSON.stringify({ command: definition.name, demo: true }),
    };
  return { ...record, content };
}
