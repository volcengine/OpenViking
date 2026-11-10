import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { VikingBot } from '@openviking/react/components/viking-bot';
import type { VikingBotAttachment } from '@openviking/react/components/viking-bot';
import '@openviking/react/components/viking-bot/style.css';

const messages = [
  {
    id: 'user-1',
    createdAt: '2026-11-09T03:04:00Z',
    role: 'user' as const,
    parts: [{ type: 'text' as const, text: '帮我整理这个项目的编译目标' }],
  },
  {
    id: 'assistant-1',
    createdAt: '2026-11-09T03:04:03Z',
    role: 'assistant' as const,
    turnId: 'turn-1',
    parts: [
      {
        type: 'reasoning' as const,
        text: '先看目录结构，再确认输出位置。',
        groupId: 'activity-1',
        durationMs: 2400,
      },
      {
        type: 'tool' as const,
        id: 'call-1',
        name: 'fs_tree',
        status: 'completed' as const,
        groupId: 'activity-1',
        input: { path: 'viking://resources' },
      },
      {
        type: 'tool' as const,
        id: 'call-find',
        name: 'find',
        status: 'completed' as const,
        groupId: 'activity-1',
        input: { query: '编译目标' },
        results: [
          {
            uri: 'viking://resources/compiled/README.md',
            label: 'README.md',
            score: 0.93,
          },
        ],
      },
      {
        type: 'tool' as const,
        id: 'call-error',
        name: 'fs_read',
        status: 'error' as const,
        groupId: 'activity-1',
        input: { path: 'viking://resources/missing.md' },
        output: '示例：资源不存在，未执行服务端操作。',
      },
      {
        type: 'text' as const,
        text: [
          '## 编译目标建议',
          '',
          '建议将项目资料整理到 `viking://resources/compiled`，并保留来源。',
          '',
          '先核对目录内容，再选择目标；确认后由宿主执行实际编译。',
          '',
          '- 检查输入资料是否完整',
          '- 保留原始路径与引用关系',
          '- 在输出目录检查结果',
          '',
          '```text',
          'ov ls viking://resources/compiled',
          '```',
          '',
          '以上仅为固定展示数据，没有执行服务端操作。',
        ].join('\n'),
        format: 'markdown' as const,
      },
    ],
    action: { key: 'commit', label: '提交会话', status: 'idle' as const },
  },
  {
    id: 'system-1',
    role: 'system' as const,
    tone: 'error' as const,
    parts: [{ type: 'text' as const, text: '示例提示：这是一条系统消息。' }],
  },
];

const ATTACHED: VikingBotAttachment[] = [
  { key: 'a1', name: 'report.pdf', status: 'ready' },
];

export default function VikingBotBasic() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [draft, setDraft] = useState('');
  const [opened, setOpened] = useState('');
  return (
    <div style={{ height: 520 }}>
      <VikingBot
        messages={messages}
        locale={componentLocale}
        onOpenUri={setOpened}
        draft={draft}
        attachments={ATTACHED}
        onDraftChange={setDraft}
        onSend={() => setDraft('')}
        onCancel={() => undefined}
        onFilesAdded={() => undefined}
      />
      <p role="status">{opened ? `打开资源：${opened}` : ''}</p>
    </div>
  );
}
