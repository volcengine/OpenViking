import { useState } from 'react';
import type { VikingBotInteractionAnswer } from '@openviking/react/components/viking-bot';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import '@openviking/react/components/viking-bot/style.css';

export default function VikingBotInteractionBasic() {
  const [answer, setAnswer] = useState<VikingBotInteractionAnswer>();
  const [submitting, setSubmitting] = useState(false);

  return (
    <div style={{ maxWidth: 520 }}>
      <VikingBotInteraction
        request={{
          kind: 'choice',
          question: '把编译结果写到哪个目标？',
          options: [
            {
              value: 'compiled',
              label: 'viking://resources/compiled',
              description: '推荐，与现有目录结构一致',
            },
            { value: 'archive', label: 'viking://resources/archive' },
          ],
          allowCustomInput: { placeholder: '输入自定义 URI' },
        }}
        index={0}
        total={2}
        submitting={submitting}
        onSubmit={(value) => {
          setSubmitting(true);
          setTimeout(() => {
            setAnswer(value);
            setSubmitting(false);
          }, 400);
        }}
        onCancel={() => setAnswer(undefined)}
      />
      {answer ? (
        <pre style={{ marginTop: 12 }}>{JSON.stringify(answer, null, 2)}</pre>
      ) : null}
    </div>
  );
}
