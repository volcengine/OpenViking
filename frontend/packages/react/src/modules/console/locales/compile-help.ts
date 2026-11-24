import type { CommandConsoleCommandDefinition } from '../../../components/command-console/index.js';
import type { Locale } from '../../../shared/locale.js';
import type { ConsoleCommandGuide } from '../types.js';

const create =
  'ov compile create --compile-name <name> --from <source-uri> --to <target-uri> --skill <skill-uri>';
const run = 'ov compile --compile-name <name>';
const status = 'ov task status <task_id>';
const guide =
  'https://docs.volcengine.com/docs/vector-database-vikingdb/compile-resource?lang=zh';

export const compileHelp = {
  'zh-CN': {
    createGuide: {
      description:
        '创建并保存编译配置，包括配置名称、来源目录、输出目录、Skill 和编译要求。创建完成后，使用 compile 按名称执行编译。',
      examples: [create],
      quickStart: {
        documentation: { label: '操作指南', href: guide },
        steps: [
          {
            description:
              '确认当前 Compile Runtime 使用的推理配置可用。内置 VikingBot Runtime 使用服务端 agent_loop 的 provider 和 model；Runtime 使用方舟接入点时，确认 OpenViking 数据库已完成配置，尚未配置则先在方舟控制台创建并关联。',
          },
          { description: '导入资料。使用 add-resource 导入需要编译的资料。' },
          {
            description: '创建编译配置。create 只保存配置，不会立即执行编译。',
            command: create,
          },
        ],
      },
    },
    runGuide: {
      description:
        '基于 ov compile create 创建的编译配置，对已导入 OpenViking 的资料进行读取、整理和加工，并将结果写入目标目录。',
      examples: [run],
      quickStart: {
        documentation: { label: '操作指南', href: guide },
        steps: [
          { description: '执行编译。使用已保存的编译配置。', command: run },
          {
            description: '状态查询。使用返回的 task_id 查询执行状态。',
            command: status,
          },
        ],
      },
    },
    args: {
      description:
        '模型和定时更新计划通过 --args 保存。下表字段均为该 JSON 对象中的字段。',
      options: [
        {
          token: 'model_name',
          description:
            '指定支持该参数的 Compile Runtime 使用的模型 endpoint ID 或模型名称。支持范围与默认模型由服务端 Runtime 配置决定；内置 VikingBot Runtime 不接受此字段。',
        },
        {
          token: 'auto_update',
          description:
            '布尔值。true 启用定时自动执行，必须同时提供有效的 update_schedule；false 关闭定时自动执行，但保留已保存的 update_schedule。省略时，有有效计划则为 true，否则为 false。',
        },
        {
          token: 'update_schedule',
          description: '使用北京时间的定时计划。auto_update 为 true 时必填。',
        },
        {
          token: 'update_schedule.frequency',
          required: true,
          description: '执行频率，仅支持 daily（每天）或 weekly（每周）。',
        },
        {
          token: 'update_schedule.weekday',
          description:
            '仅用于 weekly。1–7 对应周一至周日；daily 不接受此字段。',
        },
        {
          token: 'update_schedule.hour',
          description: '执行小时，0–23。只传 hour 时，在该小时内稳定分配分钟。',
        },
        {
          token: 'update_schedule.minute',
          description:
            '执行分钟，0–59。只传 minute 时 hour 取 0；hour 和 minute 都省略时，在 00:00–03:59 内稳定分配时刻。',
        },
      ],
    },
  },
  'en-US': {
    createGuide: {
      description:
        'Create and save a compile configuration with a name, source directories, an output directory, a Skill, and compilation requirements. Run the saved configuration by name with compile.',
      examples: [create],
      quickStart: {
        documentation: { label: 'Compilation guide', href: guide },
        steps: [
          {
            description:
              "Verify the inference configuration used by the active Compile Runtime. The built-in VikingBot Runtime uses the server's agent_loop provider and model. If the Runtime uses an Ark endpoint, make sure it is configured for the OpenViking database; create and attach one in Ark if needed.",
          },
          { description: 'Import the material to compile with add-resource.' },
          {
            description:
              'Create the compile configuration. The create command saves it without starting compilation.',
            command: create,
          },
        ],
      },
    },
    runGuide: {
      description:
        'Use a configuration created by ov compile create to process imported OpenViking material and write the results to the target directory.',
      examples: [run],
      quickStart: {
        documentation: { label: 'Compilation guide', href: guide },
        steps: [
          { description: 'Run the saved compile configuration.', command: run },
          {
            description: 'Check the run status with the returned task ID.',
            command: status,
          },
        ],
      },
    },
    args: {
      description:
        'Pass the model and scheduled update plan in --args. The fields below belong to that JSON object.',
      options: [
        {
          token: 'model_name',
          description:
            'Model endpoint ID or model name for Compile Runtimes that support this parameter. Support and the default model depend on the server Runtime configuration; the built-in VikingBot Runtime does not accept this field.',
        },
        {
          token: 'auto_update',
          description:
            'Boolean. Set to true with a valid update_schedule to enable scheduled runs. Set to false to disable scheduled runs while keeping the saved update_schedule. When omitted, it is true if a valid schedule is present and false otherwise.',
        },
        {
          token: 'update_schedule',
          description:
            'Schedule in Beijing time. Required when auto_update is true.',
        },
        {
          token: 'update_schedule.frequency',
          required: true,
          description: 'Run frequency, either daily or weekly.',
        },
        {
          token: 'update_schedule.weekday',
          description:
            'Weekly schedules only. Use 1–7 for Monday through Sunday. Daily schedules reject this field.',
        },
        {
          token: 'update_schedule.hour',
          description:
            'Hour from 0 to 23. When only hour is provided, a minute is assigned consistently within that hour.',
        },
        {
          token: 'update_schedule.minute',
          description:
            'Minute from 0 to 59. When only minute is provided, hour is 0. When both values are omitted, a time from 00:00 to 03:59 is assigned consistently.',
        },
      ],
    },
  },
} satisfies Record<
  Locale,
  {
    createGuide: ConsoleCommandGuide;
    runGuide: ConsoleCommandGuide;
    args: NonNullable<
      NonNullable<CommandConsoleCommandDefinition['parameterDetails']>['extra']
    >;
  }
>;
