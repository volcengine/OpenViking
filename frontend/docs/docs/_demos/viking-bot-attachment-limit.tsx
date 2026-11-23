import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { VikingBotComposer } from '@openviking/react/components/viking-bot';
import type { VikingBotAttachment } from '@openviking/react/components/viking-bot';
import '@openviking/react/components/viking-bot/style.css';

export default function VikingBotAttachmentLimit() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [value, setValue] = useState('');
  const [attachments, setAttachments] = useState<VikingBotAttachment[]>([]);

  return (
    <div>
      <VikingBotComposer
        locale={locale}
        value={value}
        onValueChange={setValue}
        attachments={attachments}
        onFilesAdded={(files) =>
          setAttachments((items) => [
            ...items,
            ...files.map((file) => ({
              key: crypto.randomUUID(),
              name: file.name,
              status: 'ready' as const,
            })),
          ])
        }
        onAttachmentRemove={({ key }) =>
          setAttachments((items) => items.filter((item) => item.key !== key))
        }
        onSend={() => {
          setValue('');
          setAttachments([]);
        }}
      />
      <p role="status">{attachments.length} / 10</p>
    </div>
  );
}
