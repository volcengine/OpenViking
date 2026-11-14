import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { Checkbox } from '#demo-controls';
import {
  VikingBotInteraction,
  type VikingBotInteractionAnswer,
} from '@openviking/react/components/viking-bot/interaction';
import '@openviking/react/components/viking-bot/interaction/style.css';

export default function VikingBotInteractionSteps() {
  const isZh = useLang() === 'zh';
  const [answer, setAnswer] = useState<VikingBotInteractionAnswer>();
  const [fail, setFail] = useState(false);
  return (
    <div style={{ maxWidth: 520 }}>
      <Checkbox checked={fail} onChange={setFail}>
        {isZh ? '模拟提交失败' : 'Simulate submission failure'}
      </Checkbox>
      <VikingBotInteraction
        locale={isZh ? 'zh-CN' : 'en-US'}
        request={{
          kind: 'steps',
          steps: [
            {
              kind: 'choice',
              question: isZh
                ? '把编译结果写到哪个目标？'
                : 'Where should the output go?',
              options: [
                { value: 'wiki', label: 'viking://resources/wiki' },
                { value: 'archive', label: 'viking://resources/archive' },
              ],
              allowCustomInput: {
                placeholder: isZh ? '输入自定义 URI' : 'Enter a custom URI',
              },
            },
            {
              kind: 'text',
              question: isZh ? '给这次编译起个名字' : 'Name this compilation',
              multiline: false,
            },
          ],
        }}
        onSubmit={async (value) => {
          await new Promise((resolve) => setTimeout(resolve, 600));
          if (fail)
            throw new Error(
              isZh
                ? '模拟提交失败，请重试'
                : 'Simulated failure. Please retry.',
            );
          setAnswer(value);
        }}
        onCancel={() => setAnswer(undefined)}
      />
      {answer ? <pre>{JSON.stringify(answer, null, 2)}</pre> : null}
    </div>
  );
}
