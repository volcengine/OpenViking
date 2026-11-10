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
      'tui' | 'vikingBot' | 'commandTitle' | 'commandDescription' | 'quickStart'
    >
  >,
  commands: {
    grep: '匹配资源文本',
    glob: '匹配资源路径',
    help: '查看可用命令',
    clear: '清空本地记录',
    health: '检查服务健康状态',
    status: '查看系统状态',
    ls: '列出目录内容',
    tree: '查看资源树',
    stat: '查看资源属性',
    read: '读取文件内容',
    abstract: '读取 L0 摘要',
    overview: '读取 L1 概览',
    find: '检索匹配上下文',
    search: '通过查询规划检索',
    mkdir: '创建目录',
    cp: '复制资源',
    mv: '移动资源',
    rm: '删除资源',
    write: '写入文本内容',
    'task list': '列出后台任务',
    'task status': '查看后台任务',
    'task cancel': '请求取消后台任务',
  },
  availableCommands: '可用命令',
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
