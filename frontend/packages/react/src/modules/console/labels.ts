import type { ConsoleLabels } from './types.js';

export function consoleLabels(locale: 'en' | 'zh', overrides?: ConsoleLabels) {
  return {
    ...(locale === 'zh'
      ? {
          conversation: 'VikingBot',
          command: '终端',
          mode: '面板模式',
          description: '对话服务尚未接入。可以编辑草稿，或切换到命令模式。',
          clear: '新建会话',
          history: '命令历史',
          emptyHistory: '暂无命令历史',
          commandTitle: 'OpenViking CLI',
          commandDescription: 'CLI 直接操作上下文',
          quickStart: '快速体验：',
        }
      : {
          conversation: 'VikingBot',
          command: 'Terminal',
          mode: 'Panel mode',
          description:
            'Conversation service is not connected. Edit a draft or switch to commands.',
          clear: 'New session',
          history: 'Command history',
          emptyHistory: 'No command history yet',
          commandTitle: 'OpenViking CLI',
          commandDescription: 'Work directly with your context through the CLI',
          quickStart: 'Quick start:',
        }),
    ...overrides,
  };
}
