import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { VikingBot } from '@openviking/react/components/viking-bot';
import type { VikingBotMessage } from '@openviking/react/components/viking-bot';
import '@openviking/react/components/viking-bot/style.css';

const messages: readonly VikingBotMessage[] = [
  {
    id: 'short',
    role: 'user',
    createdAt: '2026-11-09T03:04:00Z',
    parts: [{ type: 'text', text: '你好' }],
  },
  {
    id: 'with-files',
    role: 'user',
    createdAt: '2026-11-09T03:05:00Z',
    parts: [
      {
        type: 'attachment',
        name: 'README.md',
        uri: 'viking://resources/README.md',
      },
      {
        type: 'attachment',
        name: 'notes.md',
        uri: 'viking://resources/notes.md',
      },
      {
        type: 'attachment',
        name: 'report.pdf',
        uri: 'viking://resources/report.pdf',
      },
      {
        type: 'text',
        format: 'plain',
        text: '请整理这些资料，并保留来源。\nhttps://example.com/resources/a-long-path-without-spaces-to-check-message-wrapping',
      },
    ],
  },
  {
    id: 'files-only',
    role: 'user',
    createdAt: '2026-11-09T03:06:00Z',
    parts: [
      {
        type: 'attachment',
        name: 'summary.md',
        uri: 'viking://resources/summary.md',
      },
    ],
  },
];

export default function VikingBotUserMessages() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [result, setResult] = useState('');
  return (
    <>
      <div style={{ height: 440 }}>
        <VikingBot
          messages={messages}
          locale={locale}
          onOpenUri={setResult}
          onCopy={setResult}
        />
      </div>
      <output
        aria-live="polite"
        style={{
          display: 'block',
          overflowWrap: 'anywhere',
          whiteSpace: 'pre-wrap',
        }}
      >
        {result}
      </output>
    </>
  );
}
