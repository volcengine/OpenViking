import { compileHelp } from './compile-help.js';
import { transferSession } from './transfer-session.zh-CN.js';
import { coreParameters } from './core-parameters.js';
import filesystemParameters from './filesystem-parameters.js';
import { SEARCH_PARAMETERS_ZH_CN } from './search-parameters.js';
import type { renderErrors as EnglishRenderErrors } from './en-US.js';
import type { ConsoleLabels } from '../types.js';
import type enUS from './en-US.js';

const messages: typeof enUS = {
  labels: {
    conversation: 'VikingBot',
    command: '终端',
    mode: '面板模式',
    description: '连接你的资料、记忆与上下文，让每一份数据都成为可用的知识',
    clear: '新建会话',
    history: '命令历史',
    emptyHistory: '暂无命令历史',
  } satisfies Required<
    Omit<
      ConsoleLabels,
      | 'commandConsole'
      | 'vikingBot'
      | 'activationTitle'
      | 'activationDescription'
      | 'commandTitle'
      | 'commandDescription'
      | 'quickStart'
    >
  >,
  noTasks: '暂无任务',
  grepLimitReached: '已达到搜索上限，可能还有其他匹配结果。',
  telemetryResult: (value: string) => `遥测：${value}`,
  taskResult: '任务结果',
  memoryCommit: {
    idle: '提交会话',
    pending: '会话提交中',
    submitted: '会话提交中',
    success: '会话已提交',
    empty: '当前会话暂无可生成的新记忆',
    skipped: '本轮未生成新记忆',
    failed: '会话提交失败',
    completed: '记忆提取已完成',
    saved: '记忆已生成，并保存至目录文件',
    view: '查看记忆',
  },
  compileQuickInputs: {
    createWiki: {
      label: '创建 Wiki',
      prompt: '将选中的目录中的文件整理成 Wiki，保留来源和文档之间的关联',
    },
    dailyReport: {
      label: '定时日报',
      prompt: '基于选中的目录生成日报，并设置定时更新',
    },
  },
  commands: {
    ...transferSession,
    'add-resource': '导入网页链接、仓库或本地文件',
    'add-skill': '添加 Skill 到技能目录',
    'add-memory': '直接添加记忆',
    compile: '执行编译',
    'compile create': '创建编译配置，供 compile 执行',

    grep: '匹配资源文本',
    glob: '匹配资源路径',
    help: '查看可用命令',
    clear: '清空本地记录',
    health: '检查服务健康状态',
    status: '查看系统状态',
    ls: '列出目录内容',
    tree: '查看资源树',
    stat: '查看资源属性',
    read: '读取精确资源内容',
    abstract: '读取 L0 摘要',
    overview: '读取 L1 概览',
    find: '语义检索相关上下文',
    search: '通过查询规划检索',
    mkdir: '创建目录',
    cp: '复制资源',
    mv: '移动资源',
    rm: '删除资源',
    write: '更新已有资源',
    'task list': '列出后台任务',
    'task status': '查看后台任务',
    'task cancel': '请求取消后台任务',
  },
  localResourceImportHelp:
    '传入网页链接或仓库地址可直接导入。直接执行命令或仅传入本地路径，会打开「本地上传」页面，可选择文件或文件夹。',
  commandGuides: {
    'add-resource': {
      description:
        '传入网页链接或仓库地址可直接导入。导入本地文件时，请在浏览器中选择文件。',
      examples: [
        'ov add-resource https://github.com/volcengine/OpenViking/blob/main/docs/repository/README_CN.md',
      ],
    },
    'add-skill': {
      description: '从远程地址安装 Skill，可通过 -p 选择目标技能目录。',
      examples: [
        'ov add-skill https://github.com/volcengine/OpenViking/tree/skills/llm-wiki -p viking://agent/skills',
      ],
    },
    'add-memory': {
      description: '将事实、偏好或协作约定写入指定会话，再提交会话以提取记忆。',
      examples: [
        'ov add-memory "评审文档优先用表格对比方案" --session-id ov-command-console-demo',
      ],
    },
    find: {
      description:
        '按搜索词在当前上下文中做语义检索，返回匹配的资源、记忆或技能。',
      examples: ['ov find "OpenViking 有什么价值？"'],
    },
    'compile create': compileHelp['zh-CN'].createGuide,
    compile: compileHelp['zh-CN'].runGuide,
  },
  compileArgsHelp: compileHelp['zh-CN'].args,
  parameters: {
    path: '要导入的远程路径或 URL',
    to: '写入的目标 URI',
    parent: '已存在的父目录 URI',
    'parent-auto-create': '父目录不存在时创建',
    reason: '导入原因',
    args: '以 JSON 对象提供解析参数',
    'compile-args':
      '合法的 JSON 对象，用于保存模型和定时计划。字段名区分大小写。',
    'target-uri': 'Skill 的目标根目录',
    content: '要写入记忆的纯文本',
    'session-id': '要写入并提交的会话 ID',
    limit: '最多返回的条目数量',
    'compile-name':
      '汉字、ASCII 字母、数字、下划线或连字符，区分大小写，首字符不能为连字符。纯 ASCII 名称最多 64 个字符，含汉字的名称总长最多 32 个字符。同一用户内不可重名，不支持改名',
    from: '已存在的来源目录 URI，可重复传入 --from，或用英文逗号分隔多个 URI',
    'compile-from':
      '至少一个已存在且可读的来源目录，不接受单个文件。可重复传入 --from，或用英文逗号分隔多个 URI，重复项自动去重。',
    'compile-to':
      '可写的产物输出目录，建议使用独立的 viking://resources/… 目录。目录不存在时，服务端可创建。',
    skill: '用于编译的 Skill 目录 URI，决定如何读取、整理和输出资料',
    instruction:
      '补充语言、结构、引用及处理要求。省略时按 Skill 默认行为执行。',
    'task-type': '按任务类型过滤，例如 add_resource、add_skill',
    'task-status': '按任务状态过滤，例如 pending、running、completed、failed',
    'task-id': '异步任务 ID',
    ...coreParameters['zh-CN'],
    ...filesystemParameters['zh-CN'],
    ...SEARCH_PARAMETERS_ZH_CN,
  },
  commandGroups: {
    transfer: '文件传输',
    session: '会话',
    core: '核心流程',
    filesystem: '文件系统',
    search: '搜索与上下文',
    status: '配置与状态',
  },
  messages: {
    localResourceImportOpened: () => '请在本地上传页面选择文件或文件夹。',
    localResourceImportFlags: () =>
      '请在本地上传页面配置导入，或使用 --temp-file-id 按命令选项导入。',
    cancelled: () => '请求已取消；服务端可能仍在处理已接收的操作。',
    unavailableCommand: (value?: string) => `当前工作台不支持命令：${value}`,
    invalidOvpack: () => '请选择有效的 .ovpack 文件。',
    invalidOvpackDownload: () => '服务端未返回有效的 OVPack 压缩包。',
    missingUploadId: () => '上传未返回临时文件 ID。',
    unknownCommand: () => '未知命令。输入 ov help 查看可用命令。',
    shellSyntax: () => '不支持 Shell 语法。',
    unclosedQuote: () => '引号或转义未闭合。',
    unexpectedArgument: (value: string) => `未知参数：${value}`,
    duplicateArgument: (value: string) => `重复参数：${value}`,
    missingValue: (value: string) => `参数缺少值：${value}`,
    expectedUri: (value: string) => `需要 Viking URI：${value}`,
    invalidArgument: (value: string) => `无效参数 ${value}`,
    missingArgument: (value: string) => `缺少参数 ${value}`,
    compileNamedRunInputs: () =>
      '请先使用 compile create 保存输入，再使用 compile --compile-name 执行。',
  },
};
export default messages;

export const renderErrors = {
  console: '控制台显示失败',
  toolbar: '工具栏显示失败',
  command: '命令面板显示失败',
} satisfies Record<keyof typeof EnglishRenderErrors, string>;
