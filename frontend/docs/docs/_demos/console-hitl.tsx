import { useMemo, useRef, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { Button, Checkbox, Input, TextArea } from '#demo-controls';
import { Console } from '@openviking/react/modules/console';
import type { ConsoleHitlOptions } from '@openviking/react/modules/console';
import type { VikingBotInteractionRenderContext } from '@openviking/react/components/viking-bot';
import '@openviking/react/modules/console/style.css';
import { createHitlDemo } from '#console-hitl-service';

function InstructionEditor({
  context,
  initial,
  zh,
}: {
  context: VikingBotInteractionRenderContext;
  initial: string;
  zh: boolean;
}) {
  const [value, setValue] = useState(initial);
  return (
    <form
      className="ov-viking-bot__interaction-card"
      onSubmit={(event) => {
        event.preventDefault();
        void context.submit({ instruction: value });
      }}
    >
      <label>
        {zh ? '自定义指令编辑器' : 'Custom instruction editor'}
        <TextArea
          value={value}
          rows={3}
          disabled={context.disabled}
          onChange={(event) => {
            setValue(event.target.value);
            context.clearError();
          }}
        />
      </label>
      <div className="ov-viking-bot__interaction-footer">
        <Button
          type="outline"
          disabled={context.disabled}
          onClick={context.cancel}
        >
          {zh ? '取消' : 'Cancel'}
        </Button>
        <Button
          type="primary"
          htmlType="submit"
          disabled={context.disabled}
          loading={context.submitting}
        >
          {zh ? '提交' : 'Submit'}
        </Button>
      </div>
    </form>
  );
}

export default function ConsoleHitl() {
  const zh = useLang() === 'zh';
  const [fail, setFail] = useState(false);
  const [copied, setCopied] = useState('');
  const state = useRef({ zh, fail });
  state.current = { zh, fail };
  const service = useMemo(
    () =>
      createHitlDemo(
        () => (state.current.zh ? 'zh-CN' : 'en-US'),
        () => state.current.fail,
      ),
    [],
  );
  const hitl: ConsoleHitlOptions = {
    cards: {
      compile_name: {
        fields: {
          value: {
            description: zh
              ? '输入 reserved 可查看外部校验错误。'
              : 'Enter reserved to see an external validation error.',
            validate: (value) =>
              Promise.resolve(
                value === 'reserved'
                  ? zh
                    ? '该名称已被使用'
                    : 'This name is already used'
                  : undefined,
              ),
            render: ({ value, onChange, disabled, controlProps }) => (
              <Input
                {...controlProps}
                value={value}
                disabled={disabled}
                onChange={(event) => onChange(event.target.value)}
              />
            ),
          },
        },
      },
      compile_target: {
        createRequest: (card) => ({
          kind: 'target',
          question: card.title,
          initialUri:
            typeof card.payload.suggested_value === 'string'
              ? card.payload.suggested_value
              : '',
          directoryPicker: {
            rows: ['wiki', 'archive', 'readonly'].map((name) => ({
              id: name,
              kind: 'entry' as const,
              type: 'directory' as const,
              depth: 0,
              name,
              uri: `viking://resources/${name}`,
            })),
          },
        }),
        fields: {
          value: {
            validate: (uri) =>
              uri.endsWith('/readonly')
                ? zh
                  ? '该目录为只读目录'
                  : 'This directory is read-only'
                : undefined,
          },
        },
      },
      compile_instruction: {
        createRequest: (card) => ({
          kind: 'custom',
          question: card.title,
          render: (context) => (
            <InstructionEditor
              context={context}
              initial={
                typeof card.payload.current_value === 'string'
                  ? card.payload.current_value
                  : ''
              }
              zh={zh}
            />
          ),
        }),
        serialize: (answer) =>
          (answer.value as { instruction: string }).instruction,
        summarize: (value) => (typeof value === 'string' ? value : ''),
      },
      compile_schedule: {
        fields: {
          time: {
            description: zh
              ? '外部规则：更新时间须在 09:00–18:00。'
              : 'External rule: updates must be between 09:00 and 18:00.',
            validate: (value) =>
              value < '09:00' || value > '18:00'
                ? zh
                  ? '请选择 09:00–18:00 的时间'
                  : 'Choose a time between 09:00 and 18:00'
                : undefined,
          },
        },
      },
    },
  };
  return (
    <>
      <p>
        {zh
          ? '模拟服务，不访问后端。发送任意消息触发问题组，逐项提交后继续对话。'
          : 'Simulated service, with no backend calls. Send any message to request input, then submit each card to continue.'}
      </p>
      <Checkbox checked={fail} onChange={setFail}>
        {zh ? '模拟答案发送失败' : 'Simulate answer send failure'}
      </Checkbox>
      <div style={{ height: 720, marginTop: 12 }}>
        <Console
          locale={zh ? 'zh-CN' : 'en-US'}
          modes={['conversation']}
          chatOptions={{ service, hitl }}
          conversation={{ onCopy: setCopied }}
        />
      </div>
      {copied ? <pre>{copied}</pre> : null}
    </>
  );
}
