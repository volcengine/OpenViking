import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import {
  ContextPicker,
  type ContextPickerEntry,
} from '@openviking/react/components/context-picker';
import {
  VikingBotInteraction,
  type VikingBotInteractionCustomInputContext,
} from '@openviking/react/components/viking-bot/interaction';
import { useContextPickerDemo } from '#context-picker-demo';
import '@openviking/react/components/context-picker/style.css';
import '@openviking/react/components/viking-bot/interaction/style.css';

export default function VikingBotCustomDirectory() {
  const isZh = useLang() === 'zh';
  const data = useContextPickerDemo('target', isZh);
  const [context, setContext] =
    useState<VikingBotInteractionCustomInputContext>();
  const [value, setValue] = useState<ContextPickerEntry[]>([]);
  const [answer, setAnswer] = useState('');
  const locale = isZh ? 'zh-CN' : 'en-US';
  return (
    <>
      <VikingBotInteraction
        locale={locale}
        request={{
          kind: 'choice',
          question: isZh
            ? 'Wiki 保存目录位置？'
            : 'Where should the wiki be saved?',
          options: [
            {
              value: 'viking://resources/compiled',
              label: 'viking://resources/compiled',
            },
          ],
          allowCustomInput: {
            open: Boolean(context),
            onOpen: (next) => {
              setValue(
                next.value
                  ? [{ uri: next.value, name: next.value, type: 'directory' }]
                  : [],
              );
              data.onQueryChange('');
              setContext(next);
            },
          },
        }}
        labels={{ other: isZh ? '自定义目录位置' : 'Custom directory' }}
        onSubmit={(next) => setAnswer(String(next.value))}
      />
      <ContextPicker
        {...data}
        open={Boolean(context)}
        value={value}
        onChange={setValue}
        locale={locale}
        onCancel={() => setContext(undefined)}
        onConfirm={(entries) => {
          if (entries[0]) context?.select(entries[0].uri);
          setContext(undefined);
        }}
      />
      {answer ? <p>{answer}</p> : null}
    </>
  );
}
