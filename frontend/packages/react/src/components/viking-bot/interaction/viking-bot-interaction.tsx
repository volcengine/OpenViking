import { ChoiceField } from './fields/choice.js';
import { RepairField } from './fields/repair.js';
import { ScheduleField } from './fields/schedule.js';
import { TextField } from './fields/text.js';
import {
  DEFAULT_INTERACTION_LABELS,
  type InteractionLabels,
} from './labels.js';
import type {
  VikingBotInteractionAnswer,
  VikingBotInteractionProps,
} from './types.js';

function renderBody(
  props: VikingBotInteractionProps,
  labels: InteractionLabels,
  blocked: boolean,
  submit: (answer: VikingBotInteractionAnswer) => void,
) {
  const { request } = props;
  if (request.kind === 'custom') {
    return request.render({
      disabled: blocked,
      submitting: props.submitting,
      submit: (value) => submit({ kind: 'custom', value }),
      cancel: () => props.onCancel?.(),
    });
  }
  if (request.kind === 'choice') {
    return (
      <ChoiceField
        request={request}
        disabled={blocked}
        labels={labels}
        onSubmit={submit}
      />
    );
  }
  if (request.kind === 'text') {
    return (
      <TextField
        initialValue={request.initialValue}
        description={request.description}
        multiline={request.multiline}
        disabled={blocked}
        labels={labels}
        onSubmit={submit}
      />
    );
  }
  if (request.kind === 'schedule') {
    return (
      <ScheduleField
        initialValue={request.initialValue}
        timezone={request.timezone}
        disabled={blocked}
        labels={labels}
        onSubmit={submit}
      />
    );
  }
  return (
    <RepairField
      request={request}
      disabled={blocked}
      labels={labels}
      onSubmit={submit}
    />
  );
}

/** 占位组件：仅按 props 契约渲染可用视图，未经设计评审，真实实现待重写。 */
export function VikingBotInteraction({
  request,
  className,
  index = 0,
  total = 1,
  disabled,
  submitting,
  error,
  onSubmit,
  onCancel,
  labels: overrides,
}: VikingBotInteractionProps) {
  const labels = { ...DEFAULT_INTERACTION_LABELS, ...overrides };
  const blocked = Boolean(disabled || submitting);
  const submit = (answer: VikingBotInteractionAnswer) => {
    void onSubmit?.(answer);
  };

  return (
    <section
      className={['ov-viking-bot__interaction', className]
        .filter(Boolean)
        .join(' ')}
      aria-label={labels.title}
      aria-busy={submitting || undefined}
    >
      <header className="ov-viking-bot__interaction-header">
        <span className="ov-viking-bot__interaction-title">{labels.title}</span>
        {total > 1 ? (
          <span className="ov-viking-bot__interaction-progress">
            {labels.progress(index, total)}
          </span>
        ) : null}
      </header>
      {request.question ? (
        <p className="ov-viking-bot__interaction-question">
          {request.question}
        </p>
      ) : null}
      {error ? (
        <p className="ov-viking-bot__interaction-error" role="alert">
          {error}
        </p>
      ) : null}
      {renderBody({ request, submitting, onCancel }, labels, blocked, submit)}
      {onCancel ? (
        <button
          type="button"
          className="ov-viking-bot__interaction-cancel"
          disabled={blocked}
          onClick={() => onCancel()}
        >
          {labels.cancel}
        </button>
      ) : null}
    </section>
  );
}
