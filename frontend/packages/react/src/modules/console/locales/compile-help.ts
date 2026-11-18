import type { CommandConsoleCommandDefinition } from '../../../components/command-console/index.js';
import type { Locale } from '../../../shared/locale.js';
import type { ConsoleCommandGuide } from '../types.js';

const create =
  'ov compile create --compile-name <name> --from <source-uri> --to <target-uri> --skill <skill-uri>';
const run = 'ov compile --compile-name <name>';
const status = 'ov task status <task_id>';

export const compileHelp = {
  'zh-CN': {
    guide: {
      description:
        '使用 ov compile create 创建编译配置，按指定的 Skill 整理和加工已导入 OpenViking 的资料，并将产物写入目标目录。可通过 --args 设置定时自动增量更新。',
      examples: ['ov compile --compile-name demo-wiki'],
      quickStart: {
        documentation: {
          label: '操作指南',
          href: 'https://docs.openviking.ai/zh/context-compilation/01-overview',
        },
        steps: [
          {
            description:
              '确认推理配置。确认 OpenViking 已配置编译所需的推理服务。',
          },
          { description: '导入资料。使用 add-resource 导入待编译的资料。' },
          {
            description: '创建编译配置。create 只保存配置，不会立即执行编译。',
            command: create,
          },
          {
            description:
              '执行编译。使用已保存的来源目录、目标目录和 Skill 执行编译。',
            command: run,
          },
          {
            description: '状态查询。使用返回的 task_id 查询执行状态。',
            command: status,
          },
        ],
      },
    },
    args: {
      description:
        '模型和定时计划在创建时通过 --args 保存。下表列出该 JSON 对象的字段。',
      options: [
        {
          token: 'model_name',
          description: '指定编译使用的模型。可用名称取决于服务端配置。',
        },
        {
          token: 'auto_update',
          description:
            '布尔值。true 启用定时执行，须提供有效计划。false 关闭定时执行。',
          defaultValue: '有计划时为 true，否则为 false',
        },
        {
          token: 'update_schedule',
          description:
            '北京时间（Asia/Shanghai）的定时计划。auto_update 为 true 时必填。',
        },
        {
          token: 'update_schedule.frequency',
          required: true,
          description:
            '定时计划的执行频率。支持 daily（每天）和 weekly（每周）。提供计划时必填。',
        },
        {
          token: 'update_schedule.weekday',
          description: '仅用于 weekly。1–7 对应周一至周日。',
          defaultValue: '按配置稳定分配一天',
        },
        {
          token: 'update_schedule.hour',
          description: '执行小时，0–23。只传 minute 时，hour 取 0。',
        },
        {
          token: 'update_schedule.minute',
          description: '执行分钟，0–59。只传 hour 时，在该小时内稳定分配分钟。',
          defaultValue: 'hour 和 minute 都省略时，在 00:00–03:59 内稳定分配',
        },
      ],
    },
  },
  'en-US': {
    guide: {
      description:
        'Use ov compile create to save a configuration. The selected Skill processes material imported into OpenViking and writes the output to the target directory. Use --args to schedule incremental updates.',
      examples: ['ov compile --compile-name demo-wiki'],
      quickStart: {
        documentation: {
          label: 'Compilation guide',
          href: 'https://docs.openviking.ai/en/context-compilation/01-overview',
        },
        steps: [
          {
            description:
              'Verify that OpenViking has an inference service configured for compilation.',
          },
          { description: 'Import the source material with add-resource.' },
          {
            description:
              'Create a configuration. The create command saves it for later execution.',
            command: create,
          },
          {
            description:
              'Run compilation with the saved source directories, target directory, and Skill.',
            command: run,
          },
          {
            description: 'Check the run status with the returned task ID.',
            command: status,
          },
        ],
      },
    },
    args: {
      description:
        'Pass model and scheduling settings in --args when creating the configuration. The fields below belong to that JSON object.',
      options: [
        {
          token: 'model_name',
          description:
            'Model used for compilation. Supported names depend on the server configuration.',
        },
        {
          token: 'auto_update',
          description:
            'Boolean. Set to true with a valid schedule to enable scheduled runs. Set to false to disable scheduling.',
          defaultValue: 'true with a schedule, false otherwise',
        },
        {
          token: 'update_schedule',
          description:
            'Schedule in Beijing time (Asia/Shanghai). Required when auto_update is true.',
        },
        {
          token: 'update_schedule.frequency',
          required: true,
          description:
            'Run frequency, either daily or weekly. Required when a schedule is provided.',
        },
        {
          token: 'update_schedule.weekday',
          description:
            'Weekly schedules only. Use 1–7 for Monday through Sunday.',
          defaultValue: 'Day assigned consistently per configuration',
        },
        {
          token: 'update_schedule.hour',
          description:
            'Hour from 0 to 23. When only minute is provided, hour defaults to 0.',
        },
        {
          token: 'update_schedule.minute',
          description:
            'Minute from 0 to 59. When only hour is provided, a minute is assigned within that hour.',
          defaultValue:
            'A time from 00:00 to 03:59 when both hour and minute are omitted',
        },
      ],
    },
  },
} satisfies Record<
  Locale,
  {
    guide: ConsoleCommandGuide;
    args: NonNullable<
      NonNullable<CommandConsoleCommandDefinition['parameterDetails']>['extra']
    >;
  }
>;
