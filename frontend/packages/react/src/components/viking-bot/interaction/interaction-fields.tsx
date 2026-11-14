import { Input, TextArea } from '../../../shared/primitives/input/input.js';
import { ChoiceInput } from './choice-input.js';
import { ScheduleInput } from './schedule-input.js';
import type { FormError, FormRequest } from './form-value.js';
import type { InteractionLabels } from './labels.js';
import type { Locale } from '../../../shared/locale.js';
import type { VikingBotInteractionAnswer } from './types.js';

export function InteractionFields({
  request,
  labels,
  locale,
  id,
  answer,
  disabled,
  validation,
  onChange,
}: {
  request: FormRequest;
  labels: InteractionLabels;
  locale: Locale;
  id: string;
  answer?: VikingBotInteractionAnswer;
  disabled: boolean;
  validation?: FormError;
  onChange: (answer: VikingBotInteractionAnswer) => void;
}) {
  if (request.kind === 'choice' || request.kind === 'repair')
    return (
      <ChoiceInput
        request={request}
        labels={labels}
        locale={locale}
        value={answer?.kind === request.kind ? answer.value : undefined}
        disabled={disabled}
        invalid={Boolean(validation)}
        errorId={`${id}-error`}
        onChange={(value) => onChange({ kind: request.kind, value })}
      />
    );
  if (request.kind === 'schedule')
    return (
      <ScheduleInput
        request={request}
        value={
          answer?.kind === 'schedule' ? answer.value : request.initialValue
        }
        labels={labels}
        locale={locale}
        disabled={disabled}
        invalid={validation}
        errorId={`${id}-error`}
        onChange={(value) => onChange({ kind: 'schedule', value })}
      />
    );
  const props = {
    id: `${id}-answer`,
    name: 'answer',
    value: answer?.kind === 'text' ? answer.value : '',
    disabled,
    required: request.required !== false,
    'aria-invalid': Boolean(validation),
    'aria-describedby':
      [request.description && `${id}-description`, validation && `${id}-error`]
        .filter(Boolean)
        .join(' ') || undefined,
    onChange: (
      event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
    ) => onChange({ kind: 'text', value: event.target.value }),
  };
  return (
    <>
      <label className="ov-viking-bot__interaction-label" htmlFor={props.id}>
        {request.question ?? labels.answer}
      </label>
      {request.description ? (
        <p
          id={`${id}-description`}
          className="ov-viking-bot__interaction-description"
        >
          {request.description}
        </p>
      ) : null}
      {request.multiline !== false ? (
        <TextArea {...props} rows={3} />
      ) : (
        <Input {...props} />
      )}
    </>
  );
}
