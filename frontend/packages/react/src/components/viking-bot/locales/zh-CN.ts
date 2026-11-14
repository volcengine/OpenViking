import type { renderErrors as EnglishRenderErrors } from './en-US.js';
import type { VikingBotLabels } from '../model.js';

const messages = {
  activityRunning: '执行中',
  activityCompleted: '已执行',
  toolInput: '输入',
  toolOutput: '输出',
  similarity: '相似度',
  session: '会话',
  locateSession: '在文件目录定位会话',
  sessionHistory: '历史会话',
  newSession: '新建会话',
  emptySessions: '暂无历史会话',
  loading: '加载中…',
  loadMore: '加载更多',
  reference: '引用文件或目录',
  searchReferences: '搜索文件或目录',
  emptyReferences: '暂无匹配的文件或目录',
  removeReference: '移除引用',
  openReference: '打开 {uri}',
  expandReference: '展开 {name}',
  collapseReference: '收起 {name}',
  welcomeTitle: '欢迎使用 VikingBot',
  welcomeDescription:
    '连接你的资料、记忆与上下文，让每一份数据都成为可用的知识。',
  placeholder: '向 VikingBot 提问',
  send: '发送',
  stop: '停止',
  attach: '添加资源',
  removeAttachment: '移除附件',
  copy: '复制',
  copied: '已复制',
  reasoning: '推理',
  thinking: '思考中',
  tool: '工具调用',
  toolPending: '等待中',
  toolRunning: '正在调用',
  toolCompleted: '已调用',
  toolError: '调用失败',
  context: '上下文',
  empty: '开始对话',
  retry: '重试',
  scrollToLatest: '滚动到最新消息',
  unavailable: 'VikingBot 暂不可用',
} satisfies Required<VikingBotLabels>;

export default messages;

export const renderErrors = {
  bot: '对话区域显示失败',
  list: '消息列表显示失败',
  message: '消息显示失败',
  composer: '输入区域显示失败',
  interaction: '交互表单显示失败',
  header: '会话工具栏显示失败',
  footer: '对话附加内容显示失败',
} satisfies Record<keyof typeof EnglishRenderErrors, string>;
