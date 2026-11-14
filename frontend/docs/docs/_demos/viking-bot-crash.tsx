import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { VikingBot } from '@openviking/react/components/viking-bot';
import '@openviking/react/components/viking-bot/style.css';
import { Fault, RenderFailureDemo } from '#render-failure';

export default function VikingBotCrash() {
  const zh = useLang() === 'zh';
  const [draft, setDraft] = useState('');
  return (
    <RenderFailureDemo
      hint={
        zh
          ? '宿主传入的消息列表出错时，只有消息区显示兜底，输入区仍可编辑。'
          : 'When the host-supplied message list fails, only the message area shows the fallback; the composer stays editable.'
      }
    >
      <div style={{ height: 420 }}>
        <VikingBot
          locale={zh ? 'zh-CN' : 'en-US'}
          messages={[]}
          messageList={
            <Fault>
              <p style={{ padding: 16 }}>
                {zh ? '宿主渲染的消息列表。' : 'Host-rendered message list.'}
              </p>
            </Fault>
          }
          draft={draft}
          onDraftChange={setDraft}
          onSend={() => setDraft('')}
        />
      </div>
    </RenderFailureDemo>
  );
}
