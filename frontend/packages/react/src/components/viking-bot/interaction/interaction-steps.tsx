import { useEffect, useRef, useState } from 'react';
import { NotificationScope } from '../../../shared/feedback/notification-scope.js';
import { VikingBotInteractionView } from './viking-bot-interaction.js';
import type { InteractionLabels } from './labels.js';
import type {
  VikingBotInteractionProps,
  VikingBotInteractionRequest,
  VikingBotInteractionSingleAnswer,
} from './types.js';

export function InteractionSteps({
  request,
  labels,
  onSubmit,
  onCancel,
  ...props
}: Omit<VikingBotInteractionProps, 'request' | 'labels'> & {
  request: Extract<VikingBotInteractionRequest, { kind: 'steps' }>;
  labels: InteractionLabels;
}) {
  const root = useRef<HTMLDivElement>(null);
  const inFlight = useRef(false);
  const [index, setIndex] = useState(0);
  const [visited, setVisited] = useState(1);
  const [generation, setGeneration] = useState(0);
  const [answers, setAnswers] = useState<VikingBotInteractionSingleAnswer[]>(
    [],
  );
  const [pending, setPending] = useState(false);
  const focusedStep = useRef(`${index}:${generation}`);
  const blocked = Boolean(props.disabled || props.submitting || pending);
  // useEffect 必要性：步骤切换后将浏览器焦点移到新问题；新 DOM 在提交后才可聚焦，
  // 点击处理器无法访问尚未渲染的题目。仅导航或整组重置后聚焦，首次展示不抢焦点；无订阅需要清理。
  useEffect(() => {
    const step = `${index}:${generation}`;
    if (focusedStep.current === step) return;
    focusedStep.current = step;
    root.current?.querySelector<HTMLElement>('[data-active-step] h3')?.focus();
  }, [index, generation]);

  return (
    <div
      ref={root}
      className={['ov-viking-bot__interaction-steps', props.className]
        .filter(Boolean)
        .join(' ')}
    >
      {request.steps.slice(0, visited).map((step, stepIndex) => (
        // 保留已访问表单的实例，返回时恢复自由输入、选中状态和未提交的草稿。
        <div
          key={`${generation}:${stepIndex}`}
          hidden={stepIndex !== index}
          data-active-step={stepIndex === index ? '' : undefined}
        >
          <NotificationScope active={stepIndex === index}>
            <VikingBotInteractionView
              {...props}
              className={undefined}
              request={step}
              index={stepIndex}
              total={request.steps.length}
              disabled={blocked || stepIndex !== index}
              submitting={props.submitting || pending}
              labels={{
                ...labels,
                submit:
                  stepIndex === request.steps.length - 1
                    ? labels.submit
                    : labels.next,
              }}
              onPrevious={
                stepIndex > 0
                  ? () => {
                      if (!blocked) setIndex(stepIndex - 1);
                    }
                  : undefined
              }
              onCancel={
                onCancel
                  ? () => {
                      if (blocked) return;
                      setIndex(0);
                      setVisited(1);
                      setAnswers([]);
                      setGeneration((value) => value + 1);
                      onCancel();
                    }
                  : undefined
              }
              onSubmit={async (answer) => {
                if (blocked || inFlight.current || answer.kind === 'steps')
                  return;
                const value = [...answers.slice(0, stepIndex), answer];
                setAnswers(value);
                if (stepIndex < request.steps.length - 1) {
                  setVisited((count) => Math.max(count, stepIndex + 2));
                  setIndex(stepIndex + 1);
                  return;
                }
                inFlight.current = true;
                setPending(true);
                try {
                  await onSubmit?.({ kind: 'steps', value });
                } finally {
                  inFlight.current = false;
                  setPending(false);
                }
              }}
            />
          </NotificationScope>
        </div>
      ))}
    </div>
  );
}
