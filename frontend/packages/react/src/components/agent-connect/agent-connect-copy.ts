import type { AgentConnectLocale } from './types.js';

export const AGENT_CONNECT_COPY = {
  zh: {
    title: '连接 Agent',
    mainstream: '接入主流 Agent',
    custom: '开发自己的 Agent',
    integration: '接入',
    instructions: '完整指令',
    copy: '复制并发送给 Agent',
    copyMarkdown: '复制为 Markdown',
    copied: '已复制',
    failed: '复制失败，请重试',
    notice:
      '接入后 Agent 能读到你在 OpenViking 里能看到的内容，若接入境外服务数据有可能传到境外。',
    seeAgreement: '详见',
    agreement: '《用户协议》',
    apiKey: 'API Key',
    overseas: '境外',
  },
  en: {
    title: 'Connect Agent',
    mainstream: 'Connect mainstream agents',
    custom: 'Build your own agent',
    integration: 'Integration',
    instructions: 'Full instructions',
    copy: 'Copy and send to Agent',
    copyMarkdown: 'Copy as Markdown',
    copied: 'Copied',
    failed: 'Copy failed. Please retry.',
    notice:
      'Connected agents can read content available to you in OpenViking. Overseas services may transfer data abroad.',
    seeAgreement: ' See ',
    agreement: 'Terms of Service',
    apiKey: 'API Key',
    overseas: 'Overseas',
  },
} satisfies Record<AgentConnectLocale, Record<string, string>>;
