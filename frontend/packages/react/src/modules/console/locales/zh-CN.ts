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
      | 'commandTitle'
      | 'commandDescription'
      | 'quickStart'
    >
  >,
  commands: {
    'add-resource': '添加路径或 URL 到资源',
    'add-skill': '添加 Skill 到技能目录',
    'add-memory': '直接添加记忆',
    compile: '按 Skill 编译资料',
    'compile create': '创建编译配置',

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
  commandGuides: {
    'add-resource': {
      description:
        '把网页 URL 或仓库地址导入 Resources，供后续检索、阅读和引用。浏览器终端不接收本地文件路径。',
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
    'compile create': {
      description: '保存来源、目标和 Skill 的编译配置，不立即启动编译。',
      examples: [
        'ov compile create --from viking://resources/source --to viking://resources/wiki',
      ],
    },
    compile: {
      description:
        '默认使用内置 llm-wiki Skill 整理资料，将 Wiki 写入目标目录。可通过 --skill 指定其他 Skill。',
      examples: [
        'ov compile --from viking://resources/source --to viking://resources/wiki',
      ],
    },
  },
  parameters: {
    path: '要导入的远程路径或 URL',
    to: '写入的目标 URI',
    parent: '已存在的父目录 URI',
    'parent-auto-create': '父目录不存在时创建',
    reason: '导入原因',
    args: '以 JSON 对象提供解析参数',
    wait: '等待处理完成后返回',
    'target-uri': 'Skill 的目标根目录',
    content: '要写入记忆的纯文本',
    'session-id': '要写入并提交的会话 ID',
    query: '用于语义检索的搜索词',
    target_uri: '限制检索范围的 URI',
    limit: '最多返回的条目数量',
    level: '筛选内容层级，例如 0,1,2',
    from: '待编译的来源 URI',
    skill: '用于编译的 Skill URI',
    instruction: '编译的额外要求',
  },
  commandGroups: {
    core: '核心流程',
    filesystem: '文件系统',
    search: '搜索与上下文',
    status: '配置与状态',
  },
  messages: {
    cancelled: () => '请求已取消；服务端可能仍在处理已接收的操作。',
    unknownCommand: () => '未知命令。输入 ov help 查看可用命令。',
    shellSyntax: () => '不支持 Shell 语法。',
    unclosedQuote: () => '引号或转义未闭合。',
    unexpectedArgument: (value: string) => `未知参数：${value}`,
    duplicateArgument: (value: string) => `重复参数：${value}`,
    missingValue: (value: string) => `参数缺少值：${value}`,
    expectedUri: (value: string) => `需要 Viking URI：${value}`,
    invalidArgument: (value: string) => `无效参数 ${value}`,
    missingArgument: (value: string) => `缺少参数 ${value}`,
  },
};
export default messages;
