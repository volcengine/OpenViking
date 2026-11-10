import { useState } from 'react';
import type { VikingBotInteractionAnswer } from '@openviking/react/components/viking-bot';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import '@openviking/react/components/viking-bot/style.css';

const LABELS = {
  title: '待确认',
  submit: '提交',
  cancel: '取消',
  other: '其他',
  scheduleNone: '不启用',
  scheduleDaily: '每天',
  scheduleWeekly: '每周',
  scheduleTime: '时间',
  scheduleWeekday: '星期',
  weekdays: ['周一', '周二', '周三', '周四', '周五', '周六', '周日'],
  progress: (index: number, total: number) => `${index + 1} / ${total}`,
};

const SCHEDULE = {
  kind: 'schedule' as const,
  question: '多久重新编译一次？',
  timezone: 'Asia/Shanghai',
  initialValue: { mode: 'daily' as const, time: '10:30' },
};

export default function VikingBotInteractionBasic() {
  const [answer, setAnswer] = useState<VikingBotInteractionAnswer>();
  const [submitting, setSubmitting] = useState(false);

  const submit = (value: VikingBotInteractionAnswer) => {
    setSubmitting(true);
    setTimeout(() => {
      setAnswer(value);
      setSubmitting(false);
    }, 400);
  };

  return (
    <div style={{ display: 'grid', gap: 16, maxWidth: 520 }}>
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
        labels={LABELS}
        submitting={submitting}
        onSubmit={submit}
        onCancel={() => setAnswer(undefined)}
      />
      <VikingBotInteraction
        request={{
          kind: 'text',
          question: '给这次编译起个名字',
          initialValue: '每日编译',
          multiline: false,
        }}
        labels={LABELS}
        submitting={submitting}
        onSubmit={submit}
      />
      <VikingBotInteraction
        request={SCHEDULE}
        labels={LABELS}
        submitting={submitting}
        onSubmit={submit}
      />
      <VikingBotInteraction
        request={{
          kind: 'repair',
          question: '修复哪一个来源？',
          initialValue: 'b',
          options: [
            { id: 'a', label: '来源 A', description: '缺少权限' },
            { id: 'b', label: '来源 B', description: '推荐' },
          ],
        }}
        labels={LABELS}
        submitting={submitting}
        onSubmit={submit}
      />
      {answer ? (
        <pre style={{ margin: 0 }}>{JSON.stringify(answer, null, 2)}</pre>
      ) : null}
    </div>
  );
}
