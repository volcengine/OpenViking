import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { Checkbox } from '#demo-controls';
import type { VikingBotInteractionAnswer } from '@openviking/react/components/viking-bot';
import { useVikingBotDirectory } from '#viking-bot-directory';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import '@openviking/react/components/viking-bot/interaction/style.css';

const SCHEDULE = {
  kind: 'schedule' as const,
  question: '多久重新编译一次？',
  timezone: 'Asia/Shanghai',
  initialValue: { mode: 'daily' as const, time: '10:30' },
};

export default function VikingBotInteractionBasic() {
  const isZh = useLang() === 'zh';
  const [answer, setAnswer] = useState<VikingBotInteractionAnswer>();
  const [submitting, setSubmitting] = useState(false);
  const [disabled, setDisabled] = useState(false);
  const [fail, setFail] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [failCreate, setFailCreate] = useState(false);
  const directoryPicker = useVikingBotDirectory(isZh, readOnly, failCreate);
  const submit = async (value: VikingBotInteractionAnswer) => {
    setSubmitting(true);
    try {
      await new Promise((resolve) => setTimeout(resolve, 800));
      if (fail)
        throw new Error(
          isZh
            ? '模拟提交失败，请修改后重试'
            : 'Simulated failure. Edit and retry.',
        );
      if (value.kind === 'target')
        value = {
          kind: 'target',
          value: await directoryPicker.validate(value.value),
        };
      setAnswer(value);
    } finally {
      setSubmitting(false);
    }
  };
  const common = {
    locale: isZh ? ('zh-CN' as const) : ('en-US' as const),
    submitting,
    disabled,
    onSubmit: submit,
    onCancel: () => setAnswer(undefined),
  };
  return (
    <div>
      <div className="ov-demo-controls">
        <Checkbox checked={disabled} onChange={setDisabled}>
          {isZh ? '禁用交互' : 'Disable interactions'}
        </Checkbox>
        <Checkbox checked={fail} onChange={setFail}>
          {isZh ? '模拟提交失败' : 'Simulate submission failure'}
        </Checkbox>
        <Checkbox checked={readOnly} onChange={setReadOnly}>
          {isZh ? '模拟写权限撤销' : 'Revoke write permission'}
        </Checkbox>
        <Checkbox checked={failCreate} onChange={setFailCreate}>
          {isZh ? '模拟创建失败' : 'Simulate creation failure'}
        </Checkbox>
      </div>
      <p>
        {isZh
          ? '离线模拟：初始目录需自动展开和翻页定位；输入 error 查看搜索失败；read-only 目录不可写。'
          : 'Offline demo: the initial directory requires expansion and pagination. Search for error to simulate failure; read-only has no write permission.'}
      </p>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns:
            'repeat(auto-fit, minmax(min(100%, 360px), 360px))',
          gap: 16,
        }}
      >
        <VikingBotInteraction
          {...common}
          request={{
            kind: 'target',
            initialUri: 'viking://resources/wiki/archive',
            directoryPicker,
          }}
        />
        <VikingBotInteraction
          {...common}
          request={{
            kind: 'text',
            question: '给这次编译起个名字',
            initialValue: '每日编译',
            multiline: false,
          }}
        />
        <VikingBotInteraction {...common} request={SCHEDULE} />
        <VikingBotInteraction
          {...common}
          request={{
            kind: 'repair',
            question: '修复哪一个来源？',
            initialValue: 'b',
            options: [
              { id: 'a', label: '来源 A', description: '缺少权限' },
              { id: 'b', label: '来源 B', description: '推荐' },
            ],
          }}
        />
      </div>
      {answer ? (
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {JSON.stringify(answer, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
