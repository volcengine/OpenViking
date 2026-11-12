import { useMemo, useRef, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { VikingBot } from '@openviking/react/components/viking-bot';
import {
  useVikingBotChat,
  useVikingBotSessions,
} from '@openviking/react/modules/viking-bot';
import '@openviking/react/components/viking-bot/style.css';
import { botDemoService } from '#bot-demo-service';

export default function VikingBotHeadless() {
  const service = useMemo(() => botDemoService(), []);
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const chat = useVikingBotChat({ service, locale });
  const sessions = useVikingBotSessions({ service });
  const submission = useRef(0);
  const [draft, setDraft] = useState('');
  return (
    <div style={{ height: 640, display: 'flex' }}>
      <VikingBot
        style={{ flex: 1, minWidth: 0 }}
        locale={locale}
        messages={chat.messages}
        streaming={chat.streaming}
        draft={draft}
        onDraftChange={setDraft}
        isSending={chat.status === 'streaming' || chat.status === 'loading'}
        canSendWhileStreaming={
          chat.canSteer &&
          chat.status === 'streaming' &&
          Boolean(chat.sessionId)
        }
        isSubmitting={chat.isSubmitting}
        onSend={(input) => {
          const token = ++submission.current;
          setDraft((current) =>
            current.trim() === input.text.trim() ? '' : current,
          );
          void chat.send(input).then((success) => {
            if (success) void sessions.refresh();
            else if (token === submission.current)
              setDraft((current) => (current === '' ? input.text : current));
          });
        }}
        onCancel={
          chat.canInterrupt && chat.status === 'streaming'
            ? () => {
                void chat.abort();
              }
            : undefined
        }
        sessions={{
          items: sessions.sessions,
          currentId: chat.sessionId,
          isLoading: sessions.isLoading,
          error: sessions.error?.message,
          onSelect: (id) => {
            submission.current++;
            chat.selectSession(id);
            setDraft('');
          },
          onNew: () => {
            submission.current++;
            chat.reset();
            setDraft('');
          },
          onOpenHistory: () => {
            void sessions.refresh();
          },
        }}
        footer={
          chat.error ? <p role="alert">{chat.error.message}</p> : undefined
        }
      />
    </div>
  );
}
