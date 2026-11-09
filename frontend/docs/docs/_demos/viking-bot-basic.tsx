import { useState } from 'react';
import { VikingBot } from '@openviking/react/components/viking-bot';
import type { VikingBotAttachment } from '@openviking/react/components/viking-bot';
import '@openviking/react/components/viking-bot/style.css';

const messages = [
  {
    id: 'user-1',
    role: 'user' as const,
    parts: [{ type: 'text' as const, text: '帮我整理这个项目的编译目标' }],
  },
  {
    id: 'assistant-1',
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
        type: 'text' as const,
        text: '目录里有两个候选目标，建议编译到 `viking://resources/compiled`。',
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
  const [draft, setDraft] = useState('');
  return (
    <div style={{ height: 520 }}>
      <VikingBot
        messages={messages}
        draft={draft}
        attachments={ATTACHED}
        onDraftChange={setDraft}
        onSend={() => setDraft('')}
        onCancel={() => undefined}
        onFilesAdded={() => undefined}
      />
    </div>
  );
}
