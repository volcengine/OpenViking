import { TextArea } from '../../../shared/components/input/input.js';
import { useLocale } from '../../../shared/locale-context.js';
import { AskHuman } from '@ark-design/ui/ask-human';
import type { AskHumanStep } from '@ark-design/ui/ask-human';

import {
  isValidAnswer,
  toAnswer,
  toDefaultValue,
  toFields,
} from './ask-human-fields.js';
import { ScheduleInput } from './schedule-input.js';
import { resolveInteractionLabels } from './labels.js';
import type {
  VikingBotInteractionProps,
  VikingBotInteractionRequest,
} from './types.js';

const TextInput = ({
  request,
  label,
  disabled,
  onChange,
}: {
  request: Extract<VikingBotInteractionRequest, { kind: 'text' }>;
  label: string;
  disabled?: boolean;
  onChange: (value: unknown) => void;
}) => (
  <>
    {request.description ? <p>{request.description}</p> : null}
    <TextArea
      className="ov-viking-bot__interaction-input"
      defaultValue={request.initialValue}
      disabled={disabled}
      aria-label={label}
      onChange={(event) => onChange(event.target.value)}
    />
  </>
);

/**
 * 待确认交互区。表单由 Ark 的 AskHuman 渲染；需要树选择器、上传器等宿主
 * 能力时，`custom` 直接交给宿主渲染。
 *
 * The pending-interaction area. Ark's AskHuman renders the form; when a question
 * needs a tree picker or an uploader, `custom` hands rendering to the host.
 */
export function VikingBotInteractionView({
  request,
  className,
  requestKey,
  index = 0,
  total = 1,
  disabled,
  submitting,
  error,
  onSubmit,
  onCancel,
  locale: localeProp,
  labels: overrides,
}: VikingBotInteractionProps) {
  const locale = useLocale(localeProp);
  const labels = resolveInteractionLabels(locale, overrides);
  const blocked = Boolean(disabled || submitting);
  // 问题身份变化时必须重挂表单，否则字段的实例状态会把上一题答案带到新题。
  const formKey = requestKey ?? `${request.kind}:${index}`;
  const rootClass = ['ov-viking-bot__interaction', className]
    .filter(Boolean)
    .join(' ');

  if (request.kind === 'custom') {
    return (
      <section
        className={rootClass}
        aria-label={labels.title}
        aria-busy={submitting || undefined}
      >
        {error ? (
          <p className="ov-viking-bot__interaction-error" role="alert">
            {error}
          </p>
        ) : null}
        {request.render({
          disabled: blocked,
          submitting,
          submit: (value) => void onSubmit?.({ kind: 'custom', value }),
          cancel: () => onCancel?.(),
        })}
      </section>
    );
  }

  const steps: AskHumanStep[] = [
    {
      key: request.kind,
      title: request.question,
      fields: toFields(request, labels.other),
      extra: { index, total },
    },
  ];

  return (
    <section
      className={rootClass}
      aria-label={labels.title}
      aria-busy={submitting || undefined}
    >
      {total > 1 ? (
        <p className="ov-viking-bot__interaction-progress">
          {labels.progress(index, total)}
        </p>
      ) : null}
      {error ? (
        <p className="ov-viking-bot__interaction-error" role="alert">
          {error}
        </p>
      ) : null}
      <AskHuman
        key={formKey}
        steps={steps}
        current={0}
        defaultValue={toDefaultValue(request)}
        showSkip={Boolean(onCancel)}
        skipText={labels.cancel}
        submitText={labels.submit}
        onSkip={() => {
          if (blocked) return;
          onCancel?.();
        }}
        onSubmit={async (value) => {
          if (blocked) return;
          // 选择类问题必须提交有效选项，否则用户看到的选择与提交内容不一致。
          if (!isValidAnswer(request, value)) return;
          // 返回宿主的 Promise，让 Ark 的提交中状态覆盖整个异步请求。
          await onSubmit?.(toAnswer(request, value));
        }}
        customRenderFields={{
          'viking-bot-text': ({ field, onChange }) => (
            <TextInput
              label={labels.answer}
              request={
                field.extra?.request as Extract<
                  VikingBotInteractionRequest,
                  { kind: 'text' }
                >
              }
              disabled={blocked}
              onChange={onChange}
            />
          ),
          'viking-bot-schedule': ({ field, onChange }) => (
            <ScheduleInput
              request={
                field.extra?.request as Extract<
                  VikingBotInteractionRequest,
                  { kind: 'schedule' }
                >
              }
              labels={labels}
              disabled={blocked}
              onChange={onChange}
            />
          ),
        }}
      />
    </section>
  );
}
